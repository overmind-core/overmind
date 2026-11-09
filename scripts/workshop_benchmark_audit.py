import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from contextlib import suppress
from itertools import combinations
from os.path import commonprefix
from pathlib import Path

import pandas as pd

from modal_shared.decisions import decision_line, decision_reference
from overbae.services.datasets.contract import eval_input_type, training_line
from overbae.services.datasets.partition import contamination_keys
from scripts.workshop_benchmark import Platform, cli, save

ROOT = Path("output/workshop-benchmark")


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def equivalent(actual, expected):
    if expected is None or expected in ("", "NA"):
        return actual is None or actual in ("", "NA", "unknown", "Unknown")
    if type(actual) in (int, float):
        try:
            return math.isclose(actual, float(expected), rel_tol=1e-12, abs_tol=1e-12)
        except (ValueError, TypeError):
            return False
    return actual == expected


def request_payload(row):
    if "decision" in row:
        d = row["decision"]
        return {k: d[k] for k in ("state", "question", "kind", "options")}
    if "messages" in row:
        return [m for m in row["messages"] if m.get("role") != "assistant"]
    return row["input"]


def verify_source(case, original):
    supplied = {}
    hashes = {}
    for filename in case["files"]:
        path = Path(filename)
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix == ".xlsx":
            supplied[path.name] = pd.read_excel(path).to_dict("records")
        elif path.suffix == ".jsonl":
            supplied[path.name] = rows(path)
        else:
            with path.open(newline="") as stream:
                supplied[path.name] = list(
                    csv.DictReader(stream, delimiter=";" if case["key"] == "wine" else ",")
                )
    seen = set()
    for record in original.values():
        origin = record["_overmind_provenance"]["file"]
        name, index = origin["filename"], origin["row"]
        assert origin["sha256"] == hashes[name]
        assert (name, index) not in seen
        seen.add((name, index))
        expected = supplied[name][index]
        for field, value in expected.items():
            assert equivalent(record[field], value), (name, index, field)
    assert seen == {(name, i) for name, records in supplied.items() for i in range(len(records))}


def audit(key):
    directory = ROOT / key
    state = json.loads((directory / "state.json").read_text())
    attempt = state["attempts"][-1]
    assert attempt.get("collected"), "Artifacts not yet collected"
    number = attempt["number"]
    stem = f"attempt-{number}"
    pub = attempt["publication"]
    platform = Platform()
    cells = {}
    offset = 0
    while True:
        dataset = platform.call(
            "inspect_dataset",
            {
                "project_id": platform_project(),
                "dataset": state["dataset"],
                "cell_limit": 20,
                "cell_offset": offset,
            },
        )
        cells.update({c["id"]: c for c in dataset["cells"]})
        if not dataset["cell_page"]["has_more"]:
            break
        offset = int(dataset["cell_page"]["next_cursor"])
    source = next(c for c in cells.values() if c["transformation"]["execution"] == "source")
    versions = {}

    def load_cell(cell):
        if cell not in versions:
            target = directory / f"cell-{cell}.jsonl"
            if not target.exists():
                cli(
                    [
                        "dataset",
                        "export",
                        state["dataset"],
                        "--cell",
                        cell,
                        "--output",
                        str(target),
                    ],
                    directory / f"cell-{cell}.log",
                )
            versions[cell] = {r["source_row"]: r for r in rows(target)}
        return versions[cell]

    original = load_cell(source["id"])
    verify_source(state["case"], original)
    output = rows(directory / f"{stem}-output.jsonl")
    assert len(original) == state["case"]["source_rows"], (
        len(original),
        state["case"]["source_rows"],
    )
    assert len({r["source_row"] for r in output}) == len(output)

    def ancestors(record):
        result = set()
        for parent in record["_overmind_provenance"]["parents"]:
            assert parent["fingerprint"] == cells[parent["cell"]]["fingerprint"]
            if parent["cell"] == source["id"]:
                assert parent["row"] in original
                result.add(parent["row"])
            else:
                result.update(ancestors(load_cell(parent["cell"])[parent["row"]]))
        assert result
        return result

    lineage = {r["source_row"]: ancestors(r) for r in output}
    report = {
        "case": key,
        "attempt": number,
        "source_rows": len(original),
        "output_rows": len(output),
        "source_cell": source["id"],
        "output_cell": pub["cell"],
        "checks": [],
    }
    report["checks"].extend(
        [
            "Every landed source value and file checksum matches the supplied original",
            "All published rows have valid source lineage",
        ]
    )
    kinds = Counter()
    for r in output:
        if state["case"]["intent"] == "train":
            training_line(r)
        if "decision" in r:
            decision_line(r)
            d = r["decision"]
            request = {k: d[k] for k in ("state", "question", "kind", "options")}
            reference = (
                {"probabilities": d["target_probabilities"]}
                if "target_probabilities" in d
                else {"mean": d["target_mean"], "values": d["option_values"]}
            )
            decision_reference(request, reference)
            kinds[d["target_semantics"]] += 1
    report["target_semantics"] = dict(kinds)
    role_indices = {}
    role_records = {}
    role_inputs = {}
    role_keys = {}
    for member in pub.get("partition", {}).get("members", []):
        role = member["role"]
        split = rows(directory / f"{stem}-{role}.jsonl")
        indices = set()
        for row in split:
            p = row["_overmind_provenance"]["partition"]
            assert (
                p["plan"] == pub["partition"]["id"]
                and p["role"] == role
                and p["source_cell"] == pub["cell"]
            )
            i = p["source_row"]
            assert i not in indices
            indices.add(i)
            assert request_payload(row) == request_payload(output[i])
            if "decision" in row:
                assert row["decision"] == output[i]["decision"]
        role_indices[role] = indices
        role_records[role] = split
        role_inputs[role] = {json.dumps(request_payload(r), sort_keys=True) for r in split}
        role_keys[role] = set().union(
            *(contamination_keys(r, pub["partition"]["recipe"]["group_by"]) for r in split)
        )
    if state["case"]["intent"] == "train":
        assert {"train", "development", "final"} <= role_indices.keys()
        assert all(role_indices.values()), "Empty consumer partition"
    if role_indices:
        assert set.union(*role_indices.values()) == set(range(len(output)))
        for a, b in combinations(role_indices, 2):
            assert not role_indices[a] & role_indices[b]
            assert not role_inputs[a] & role_inputs[b], f"Identical inputs cross {a}/{b}"
            assert not role_keys[a] & role_keys[b], f"Inherited content/groups cross {a}/{b}"
        report["split_rows"] = {r: len(v) for r, v in role_indices.items()}
        report["checks"].append(
            "Split coverage complete; exact inputs and inherited/declared groups disjoint"
        )
    if key in {"penguins", "beans", "wine", "sms", "bikes", "dolly", "annotations"}:
        assert len(output) == len(original)
        assert all(len(v) == 1 for v in lineage.values())
        assert set.union(*lineage.values()) == set(original)

    def src(row):
        return original[next(iter(lineage[row["source_row"]]))]

    if key in {"penguins", "beans"}:
        target = "species" if key == "penguins" else "Class"
        allowed = (
            {
                "bill_length_mm",
                "bill_depth_mm",
                "flipper_length_mm",
                "body_mass_g",
                "sex",
                "island",
                "year",
            }
            if key == "penguins"
            else {
                k
                for k in next(iter(original.values()))
                if k not in {target, "source_row"} and not k.startswith("_overmind_")
            }
        )
        for r in output:
            source_row = src(r)
            d = r["decision"]
            input_state = json.loads(d["state"])
            assert input_state and set(input_state) <= allowed
            assert all(equivalent(v, source_row[k]) for k, v in input_state.items()), r[
                "source_row"
            ]
            assert d["options"][d["target_probabilities"].index(1)] == source_row[target]
        report["checks"].append(
            "Every selected feature and observed class matches source; identifiers/target excluded"
        )
        report["class_counts"] = dict(Counter(src(r)[target] for r in output))
        for role, indices in role_indices.items():
            labels = Counter(src(output[i])[target] for i in indices)
            assert set(labels) == set(report["class_counts"]), (role, labels)
        report["checks"].append("Every observed class is represented in every consumer split")
    elif key == "wine":
        for r in output:
            s = src(r)
            d = r["decision"]
            features = json.loads(d["state"])
            for k, v in features.items():
                if k in {"wine_type", "wine_colour", "wine_color", "type", "color"}:
                    filename = s["_overmind_provenance"]["file"]["filename"]
                    assert v in {"red", "white"} and v in filename
                else:
                    assert (
                        k in s
                        and k not in {"quality", "source_row", "_overmind_provenance"}
                        and equivalent(v, s[k])
                    )
            expected = s["quality"]
            if "target_mean" in d:
                assert equivalent(d["target_mean"], expected)
            else:
                index = d["target_probabilities"].index(1)
                assert equivalent(float(d["options"][index]), expected)
        report["checks"].append(
            "All ordinal scores and laboratory values preserved; no fabricated distribution"
        )
    elif key == "sms":
        for r in output:
            s = src(r)
            d = r["decision"]
            text = d["state"]
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
            assert (
                text == s["text"]
                or parsed == s["text"]
                or parsed == {"text": s["text"]}
                or parsed == {"message": s["text"]}
            )
            label = d["options"][d["target_probabilities"].index(1)].lower()
            assert label == s["label"]
        report["checks"].append("Original message bytes and spam/ham supervision preserved")
    elif key == "bikes":
        times = {}
        for i, r in enumerate(output):
            s = src(r)
            d = r["decision"]
            state = json.loads(d["state"])
            expected = {
                "date": s["dteday"],
                "hour": s["hr"],
                "month": s["mnth"],
                "weekday_sunday_0": s["weekday"],
                "holiday": s["holiday"],
                "workingday": s["workingday"],
            }
            assert set(state) == set(expected)
            assert all(equivalent(state[k], v) for k, v in expected.items())
            positive = int(s["cnt"]) >= 100
            assert d["options"] == ["No", "Yes"]
            assert d["target_probabilities"] == [float(not positive), float(positive)]
            assert d["target_semantics"] == "categorical_gold"
            times[i] = pd.Timestamp(s["dteday"]) + pd.Timedelta(hours=int(s["hr"]))
        ranges = {
            role: (min(times[i] for i in indices), max(times[i] for i in indices))
            for role, indices in role_indices.items()
        }
        assert ranges["train"][1].date() < ranges["development"][0].date()
        assert ranges["development"][1].date() < ranges["final"][0].date()
        report["time_ranges"] = {role: [str(a), str(b)] for role, (a, b) in ranges.items()}
        report["checks"].append(
            "Every threshold label matches cnt >= 100; inputs contain only verified calendar fields; full-day partitions are strictly chronological"
        )
    elif key == "dolly":
        templates = Counter()
        systems = set()
        for r in output:
            s = src(r)
            messages = r["messages"]
            user = "\n".join(m["content"] for m in messages if m["role"] == "user")
            assistant = [m["content"] for m in messages if m["role"] == "assistant"]
            assert s["instruction"] in user and (not s["context"] or s["context"] in user)
            assert assistant == [s["response"]]
            assert [m["role"] for m in messages] in (
                ["user", "assistant"],
                ["system", "user", "assistant"],
            )
            systems.add(tuple(m["content"] for m in messages if m["role"] == "system"))
            residual = user
            for value in sorted([s["instruction"], s["context"]], key=len, reverse=True):
                if value:
                    residual = residual.replace(value, "<source>")
            templates[residual] += 1
        assert len(templates) <= 2 and len(systems) <= 1, (
            "Per-example content added outside instruction/context"
        )
        report["checks"].append(
            "All source instructions/context retained; exact assistant responses preserved"
        )
    elif key == "retail":
        frame = pd.DataFrame(original.values())
        frame["time"] = pd.to_datetime(frame["InvoiceDate"])
        frame["customer"] = frame["CustomerID"].map(lambda value: str(int(value)))
        end = frame["time"].max()
        paid = frame[
            ~frame["InvoiceNo"].astype(str).str.upper().str.startswith("C")
            & (frame["Quantity"] > 0)
            & (frame["UnitPrice"] > 0)
        ]
        eligible = {}
        excluded = Counter()
        statuses = {}
        for customer, history in frame.groupby("customer"):
            orders = paid[paid["customer"] == customer]
            if orders.empty:
                excluded["no_qualifying_invoice"] += 1
                statuses[customer] = "no_qualifying_invoice"
                continue
            anchor = orders["time"].min()
            deadline = anchor + pd.Timedelta(days=30)
            if deadline > end:
                excluded["right_censored"] += 1
                statuses[customer] = "right_censored"
                continue
            basket = orders[orders["time"] == anchor]
            later = orders[(orders["time"] > anchor) & (orders["time"] <= deadline)]
            features = {
                "country": sorted(basket["Country"].unique()),
                "first_order_month": anchor.month,
                "first_order_weekday": anchor.weekday(),
                "first_order_hour": anchor.hour,
                "initial_invoice_count": basket["InvoiceNo"].nunique(),
                "initial_line_count": len(basket),
                "initial_distinct_products": basket["StockCode"].nunique(),
                "initial_quantity": int(basket["Quantity"].sum()),
                "initial_value_gbp": round(
                    float((basket["Quantity"] * basket["UnitPrice"]).sum()), 2
                ),
            }
            eligible[customer] = (history, basket, later, features, anchor, deadline)
            statuses[customer] = "eligible"
        cohort_cell = next(
            c
            for c in cells.values()
            if c["rows"] == len(statuses) and c["transformation"]["run"] == pub["run"]
        )
        cohort = list(load_cell(cohort_cell["id"]).values())
        assert len(cohort) == len(statuses) and {r["customer_id"] for r in cohort} == set(statuses)
        for r in cohort:
            assert r["status"] == statuses[r["customer_id"]]
            assert ancestors(r) == set(
                frame.loc[frame["customer"] == r["customer_id"], "source_row"]
            )
            if r["status"] != "eligible":
                assert r["label"] is None
        assert len(output) == len(eligible)
        assert {r["customer_id"] for r in output} == set(eligible)
        for r in output:
            history, basket, later, features, anchor, deadline = eligible[r["customer_id"]]
            assert lineage[r["source_row"]] == set(history["source_row"])
            assert set(r["initial_parent_rows"]) == set(basket["source_row"])
            assert set(r["repeat_parent_rows"]) == set(later["source_row"])
            d = r["decision"]
            actual = json.loads(d["state"])
            assert set(actual) == set(features)
            assert all(
                actual[k] == v if isinstance(v, list) else equivalent(actual[k], v)
                for k, v in features.items()
            )
            assert d["options"] == ["No", "Yes"]
            positive = not later.empty
            assert d["target_probabilities"] == [float(not positive), float(positive)]
            assert d["target_semantics"] == "categorical_gold"
            assert pd.Timestamp(r["anchor_time"]) == anchor
            assert pd.Timestamp(r["deadline"]) == deadline <= end
            assert "first observed" in d["question"] and "paid invoice" in d["question"]
            assert "First-ever" in d["target_provenance"]["limitations"]
        report["checks"].append(
            "Every first-observed basket feature, 30-day inclusive target, contributing invoice and exclusion independently reconstructed; no future/identity fields in model input"
        )
        report["excluded_customers"] = dict(excluded)
        report["positive_customers"] = sum(not item[2].empty for item in eligible.values())
        report["qualification"] = (
            "Historical held-out-customer proxy only: paid invoice is not verified completion; first observed is not first-ever; no forward-time performance claim"
        )
    elif key == "squad":
        expected = {
            qa["id"]: (sid, article, paragraph, qa)
            for sid, article in original.items()
            for paragraph in article["paragraphs"]
            for qa in paragraph["qas"]
        }
        observed = set()
        templates = Counter()
        for r in output:
            assert eval_input_type(r["input"]) == "messages"
            messages = r["input"]["messages"] if isinstance(r["input"], dict) else r["input"]
            assert all(m["role"] in {"system", "user"} for m in messages)
            text = "\n".join(s for m in messages for s in strings(m["content"]))
            matches = [
                (identity, article, paragraph, qa)
                for identity, (sid, article, paragraph, qa) in expected.items()
                if lineage[r["source_row"]] == {sid}
                and paragraph["context"] in text
                and qa["question"] in text
            ]
            assert len(matches) == 1, (r["source_row"], len(matches))
            identity, article, paragraph, qa = matches[0]
            assert identity not in observed
            observed.add(identity)
            assert {a["text"] for a in qa["answers"]} <= set(strings(r["expected_output"]))
            if "answer_annotations" in r:
                assert r["answer_annotations"] == qa["answers"]
                assert all(
                    paragraph["context"][a["answer_start"] : a["answer_start"] + len(a["text"])]
                    == a["text"]
                    for a in r["answer_annotations"]
                )
            residual = text
            for value in sorted(
                [paragraph["context"], qa["question"], article["title"]], key=len, reverse=True
            ):
                residual = residual.replace(value, "<source>")
            templates[residual] += 1
        assert observed == set(expected)
        assert len(templates) <= 2, (
            "Unexpected per-question content outside source passage/question/title"
        )
        report["checks"].append(
            "Every original QA represented with exact passage/question, all answer alternatives in references and article lineage"
        )
        report["questions"] = len(expected)
    elif key == "annotations":
        by_position = {i: role for role, indices in role_indices.items() for i in indices}
        disagreement = 0
        for i, r in enumerate(output):
            s = src(r)
            text = " ".join(s["post_tokens"])
            d = r["decision"]
            actual = d["state"]
            with suppress(ValueError):
                actual = json.loads(actual)
            assert d["state"] == text or actual in (text, {"text": text}, {"post": text})
            votes = Counter(a["label"] for a in s["annotators"])
            assert set(d["options"]) == {"normal", "offensive", "hatespeech"}
            assert d["target_semantics"] == "annotator_distribution"
            assert all(
                math.isclose(p, votes[option] / len(s["annotators"]), abs_tol=1e-12)
                for option, p in zip(d["options"], d["target_probabilities"], strict=True)
            )
            assert (
                by_position[i]
                == {"train": "train", "val": "development", "test": "final"}[s["official_split"]]
            )
            disagreement += len(votes) > 1
        report["checks"].append(
            "Exact post text, all annotator vote fractions and official split membership preserved; annotation metadata excluded from input"
        )
        report["disagreement_rows"] = disagreement
    elif key == "preference":
        expected = {}
        for sid, s in original.items():
            shared = commonprefix([s["chosen"], s["rejected"]])
            boundary = shared.rfind("\n\nAssistant:") + len("\n\nAssistant:")
            assert boundary >= len("\n\nAssistant:")
            expected[sid] = (
                s["chosen"][:boundary],
                s["chosen"][boundary:],
                s["rejected"][boundary:],
            )
        covered, choices = Counter(), Counter()
        orientations = set()
        for r in output:
            assert len(lineage[r["source_row"]]) == 1
            sid = next(iter(lineage[r["source_row"]]))
            conversation, chosen, rejected = expected[sid]
            d = r["decision"]
            actual = json.loads(d["state"])
            assert set(actual) == {"conversation", "response_A", "response_B"}
            assert actual["conversation"] in {
                conversation,
                conversation.removesuffix("\n\nAssistant:"),
            }
            assert actual["response_A"] != actual["response_B"], (
                f"Non-identifiable directional preference retained in training: source row {sid}"
            )
            assert {actual["response_A"], actual["response_B"]} == {chosen, rejected}
            assert d["options"] == ["A", "B"]
            winner = 0 if actual["response_A"] == chosen else 1
            assert d["target_probabilities"] == ([1.0, 0.0] if winner == 0 else [0.0, 1.0])
            assert d["target_semantics"] == "pairwise_preference"
            assert (sid, winner) not in orientations
            orientations.add((sid, winner))
            covered[sid] += 1
            choices[winner] += 1
        comparable = {sid for sid, (_, chosen, rejected) in expected.items() if chosen != rejected}
        assert set(covered) == comparable
        assert len(set(covered.values())) == 1 and set(covered.values()) <= {1, 2}
        assert set(choices) == {0, 1}, "Preferred candidate always occupies the same position"
        audit_cells = {
            parent["cell"] for r in output for parent in r["_overmind_provenance"]["parents"]
        }
        assert len(audit_cells) == 1
        retained = list(load_cell(audit_cells.pop()).values())
        retained_sources = set()
        for r in retained:
            parents = ancestors(r)
            assert len(parents) == 1
            sid = parents.pop()
            assert sid not in retained_sources
            retained_sources.add(sid)
            assert r["chosen"] == original[sid]["chosen"]
            assert r["rejected"] == original[sid]["rejected"]
            if sid in comparable:
                assert r["status"] == "active" and r["quarantine_reason"] is None
            else:
                assert r["status"] == "quarantined"
                assert r["quarantine_reason"] == "indistinguishable_alternatives"
                assert r.get("decision") is None
        assert retained_sources == set(original)
        conversation_roles = {}
        for role, indices in role_indices.items():
            for i in indices:
                sid = next(iter(lineage[output[i]["source_row"]]))
                conversation = " ".join(expected[sid][0].casefold().split())
                assert conversation_roles.setdefault(conversation, role) == role
        assert len({r["decision"]["question"] for r in output}) == 1
        report["quarantined_identical_pairs"] = len(expected) - len(comparable)
        report["retained_audit_rows"] = len(retained)
        report["retained_blank_alternative_pairs"] = sum(
            not chosen.strip() or not rejected.strip()
            for sid, (_, chosen, rejected) in expected.items()
            if sid in comparable
        )
        report["preferred_position_counts"] = dict(choices)
        report["checks"].append(
            "Every source pair retained verbatim with lineage and explicit active/quarantine status; distinguishable pairs preserve complete continuations and correct nonconstant preference positions; identical alternatives have no training label; repeated conversations stay within one role"
        )
    else:
        raise NotImplementedError(
            f"Inspect {key} representation before implementing its independent semantic audit"
        )
    report["passed"] = True
    report["objective_status"] = "qualified_proxy" if report.get("qualification") else "passed"
    save(directory / f"{stem}-audit.json", report)
    print(json.dumps(report, indent=2))


def platform_project():
    return "1e3f3e92-b50d-4590-85ed-97921d132d3c"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case")
    audit(parser.parse_args().case)
