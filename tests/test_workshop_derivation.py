import uuid
from types import SimpleNamespace

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, Project, ProjectMembership, User
from overbae.services.datasets import contract, partition, paths, semantic_checks, store, use
from overbae.services.datasets.notebook import agent, engines
from overbae.services.mcp.contracts.datasets import serialize_dataset_detail
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db

SOURCE = [
    {"text": '"The library opens at nine."', "page": 1, "_overmind_document_id": "handbook"},
    {
        "text": "Books can be borrowed for fourteen days.",
        "page": 2,
        "_overmind_document_id": "handbook",
    },
    {"text": "", "page": 3, "_overmind_document_id": "handbook"},
]


@pytest.mark.parametrize("intent", ["train", "eval"])
@pytest.mark.parametrize("interrupted", [False, True], ids=["complete", "resume"])
@pytest.mark.parametrize("initial", [False, True], ids=["chat", "initial"])
def test_source_to_examples_keeps_source_and_finishes_without_another_decision(
    monkeypatch, settings, intent, interrupted, initial
):
    settings.STRIPE_SECRET_KEY = ""
    project = Project.objects.create(name="Source preparation", slug=uuid.uuid4().hex)
    user = User.objects.create_user(email=f"{uuid.uuid4().hex}@example.test", password="test")
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "intent": intent, "source": {"rows": SOURCE}},
        format="json",
    )
    assert created.status_code == 201, created.data
    dataset = Dataset.objects.get(pk=created.data["id"])
    original_path = paths.cell_path(dataset.id, dataset.source.id)
    fingerprint = store.file_sha256(original_path)
    original = list(store.iter_rows(original_path))
    queued = []
    monkeypatch.setattr(tasks.turn, "apply_async", lambda **kwargs: queued.append(kwargs))
    receipts = []
    evidence_path = "messages.0.content" if intent == "train" else "input"
    answer_path = "messages.1.content" if intent == "train" else "expected_output"
    judged = []

    def judge(state, questions, **kwargs):
        for row in state["rows"].values():
            assert set(row) == {"source_row", evidence_path, answer_path}
            assert "Passage:" in row[evidence_path]
        judged.extend(state["rows"].values())
        return SimpleNamespace(
            parsed=SimpleNamespace(answers=dict.fromkeys(questions, "pass")),
            stats={"response_cost": 0},
            judge_trace_id=uuid.uuid4().hex,
        )

    monkeypatch.setattr(semantic_checks.decisions, "invoke", judge)

    class Engine:
        name = "fixture"
        calls = 0

        def run(self, current, message, tools, pending):
            self.calls += 1
            handlers = tools.handlers()
            if self.calls == 1:
                handlers["query"]({"sql": "SELECT * FROM t ORDER BY source_row"})
                plan = handlers["record_preparation_plan"](
                    {
                        "version": "1.0",
                        "objective": "Build passage-grounded comprehension examples",
                        "consumer": "sft" if intent == "train" else "model_evaluation",
                        "semantic_row_budget": 2,
                        "understanding": "Two readable passages and one empty page; retain the source",
                        "outcome": {
                            "deliverables": ["Comprehension examples"],
                            "task": "Answer a question using the supplied passage.",
                            "model_input": "Passage and question; the answer remains the target.",
                        },
                        "families": [
                            {
                                "name": "passages",
                                "evidence": "Inspected all three source rows",
                                "input_columns": ["text"],
                                "group_columns": ["_overmind_document_id"],
                            }
                        ],
                        "assumptions": [
                            "One grounded question per readable passage; page 3 lacks evidence"
                        ],
                        "steps": [
                            {
                                "id": "qa",
                                "kind": "transform",
                                "description": "Derive grounded Q&A, then check the consumer format",
                            }
                        ],
                        "checks": [
                            {
                                "name": "format",
                                "category": "technical",
                                "method": "deterministic",
                                "question": "Does the output satisfy its consumer?",
                            },
                            {
                                "name": "answer_support",
                                "category": "semantic",
                                "method": "semantic",
                                "question": "Is the answer supported by the supplied passage?",
                            },
                        ],
                    }
                )
                assert plan["ok"], plan
            seed = handlers["seed_examples"](
                {
                    "mode": "derive",
                    "plan_step": "qa",
                    "target_rows": 2,
                    "instruction": "One reading-comprehension question per readable passage",
                }
            )
            assert seed.get("remaining_rows") == (1 if self.calls > 1 else 2), seed
            examples = []
            for index, (question, answer) in enumerate(
                [
                    ("When does the library open?", "At nine."),
                    ("How long can books be borrowed?", "Fourteen days."),
                ]
            ):
                text = SOURCE[index]["text"]
                prompt = f"Passage: {text}\nQuestion: {question}"
                row = (
                    {
                        "messages": [
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": answer},
                        ]
                    }
                    if intent == "train"
                    else {"input": prompt, "expected_output": answer}
                )
                examples.append(
                    {"seed_row": index, "row": row, "evidence": [{"column": "text", "quote": text}]}
                )
            if self.calls == 1:
                bad = {
                    **examples[0],
                    "evidence": [{"column": "text", "quote": "The library opens at noon."}],
                }
                rejected = handlers["add_synthetic_rows"]({"examples": [examples[1], bad]})
                assert rejected["ok"] is False
                current.refresh_from_db()
                assert current.active_cell.id == dataset.source.id and current.cells.count() == 1
                first = handlers["add_synthetic_rows"]({"examples": examples[:1]})
                assert first["ok"], first
                receipts.append(first["run_id"])
                replayed = handlers["add_synthetic_rows"]({"examples": examples[:1]})
                assert replayed["id"] == first["id"] and replayed["generated_rows"] == 1
                if interrupted:
                    while pending:
                        yield pending.pop(0)
                    return engines.Outcome(error="Provider disconnected")
            done = handlers["add_synthetic_rows"]({"examples": examples[1:]})
            assert done["ok"] and done["remaining_rows"] == 0, done
            assert done["run_id"] == receipts[0] and done["id"]
            checked = handlers["check_semantic_quality"](
                {
                    "version": done["id"],
                    "max_rows": 2,
                    "checks": [
                        {
                            "name": "answer_support",
                            "question": "Is the answer supported by the supplied passage?",
                            "evidence_columns": [evidence_path],
                            "answer_columns": [answer_path],
                        }
                    ],
                }
            )
            assert checked["ok"] and not checked.get("skipped"), checked
            while pending:
                yield pending.pop(0)
            return engines.Outcome(
                text="Two grounded examples prepared; page 3 has no evidence. Answers remain model-derived."
            )

        def describe_error(self, exc):
            return str(exc)

    engine = Engine()
    monkeypatch.setattr(engines, "select", lambda user=None: engine)
    for index, message in enumerate(
        ["Turn these passages into a Q&A dataset.", "Continue."]
        if interrupted
        else ["Turn these passages into a Q&A dataset."]
    ):
        if initial and index == 0:
            tasks.diagnose.apply(
                kwargs={"dataset_id": str(dataset.id), "user_id": str(user.id)},
                task_id=str(uuid.uuid4()),
            )
            continue
        response = client.post(
            f"/api/datasets/{dataset.id}/chat/", {"message": message}, format="json"
        )
        assert response.status_code == 202, response.data
        tasks.turn.apply(kwargs=queued[-1]["kwargs"], task_id=str(uuid.uuid4()))
    dataset.refresh_from_db()
    assert dataset.chat[-1]["status"] == "complete", dataset.chat[-1]["error"]
    assert dataset.capability_id is None
    assert dataset.cells.count() == 2
    assert dataset.source.rows == 3 and store.file_sha256(original_path) == fingerprint
    active = dataset.active_cell
    output = list(store.iter_rows(paths.cell_path(dataset.id, active.id)))
    assert (
        len(output) == 2
        and contract.measure(store.read_frame(paths.cell_path(dataset.id, active.id)))[intent]["ok"]
    )
    assert active.preparation_plan["step_id"] == "qa"
    assert active.review["mode"] == "derive"
    assert active.review["source_rows"] == 3
    assert active.review["source_rows_without_examples"] == 1
    assert len(judged) == 2
    assert active.quality_report["fingerprint"] == active.fingerprint
    assert active.quality_report["checks"][0]["rows_checked"] == 2
    for index, row in enumerate(output):
        lineage = row["_overmind_provenance"]
        assert lineage["kind"] == "synthetic" and lineage["mode"] == "derive"
        assert lineage["evidence"] == [{"column": "text", "quote": SOURCE[index]["text"]}]
        assert partition.contamination_keys(original[index]) <= partition.contamination_keys(row)
    detail = serialize_dataset_detail(dataset)
    assert detail.active.rows == 2
    assert client.get(f"/api/datasets/{dataset.id}/").data["chat"][-1]["status"] == "complete"
    use.use(dataset, intent)
    after_use = agent.Tools(dataset.id, user, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "plan_step": "qa",
            "cell_id": str(active.id),
            "target_rows": 2,
            "instruction": "Continue",
        }
    )
    assert after_use["ok"], after_use
    active.refresh_from_db()
    assert active.frozen
    assert list(store.iter_rows(paths.cell_path(dataset.id, active.id))) == output
    assert after_use["published_cell"] is None
