"""``MEDIA_ROOT`` is redirected to a tmp dir; no DB."""

from __future__ import annotations

import json

from overbae.services.artifact_model import ARTIFACT_KINDS
from overbae.services.codebase import artifacts as A  # noqa: N812


def _valid_card() -> dict:
    return {
        "task": "Answer clinical questions with grounded reasoning.",
        "modality": "text",
        "domain": "clinical question answering",
        "input_schema": {"question": "str: the user's clinical question"},
        "output_fields": {"answer": "str: the answer", "reasoning": "str: the rationale"},
        "expected_output": {
            "description": "A medically accurate answer grounded in the question.",
            "example": "Start metformin 500mg…",
            "quality_signals": ["cites guidelines", "no hallucinated drugs"],
        },
        "tool_spec": [{"name": "search", "purpose": "retrieve evidence", "args": "query"}],
        "vocabulary": {"CoT": "chain-of-thought reasoning"},
        "success_criteria": ["answer is clinically correct"],
        "failure_modes": ["hallucinated dosage"],
        "provenance": {"paths": ["overbae/capability.py#L10-L40", "overbae/tools.py#L5"]},
    }


def test_extended_artifact_kinds_present():
    for kind in ("capability_card", "io_schema", "tool_spec", "vocabulary", "prompt_spec"):
        assert kind in ARTIFACT_KINDS


def test_valid_card_passes_validation():
    assert A.validate_capability_card(_valid_card()) == []


def test_validation_flags_missing_and_empty_fields():
    card = _valid_card()
    del card["task"]
    card["input_schema"] = {}
    errors = A.validate_capability_card(card)
    assert any("task" in e for e in errors)
    assert any("input_schema" in e for e in errors)


def test_validation_flags_bad_provenance():
    card = _valid_card()
    card["provenance"] = {"paths": ["overbae/capability.py"]}  # no #Lline anchor
    errors = A.validate_capability_card(card)
    assert any("provenance" in e for e in errors)

    card["provenance"] = {"paths": []}
    assert any("provenance" in e for e in A.validate_capability_card(card))


def test_non_dict_card_is_invalid():
    assert A.validate_capability_card(None)
    assert A.validate_capability_card("nope")


def test_fallback_card_is_never_empty_and_valid_with_path():
    item = {
        "name": "Data Capability",
        "description": "Analyzes datasets for quality smells.",
        "source_path": "overbae/tasks/workshop.py",
        "tools_summary": "sample_rows; query_schema",
        "capability_description": {"inputs": "a dataset", "outputs": "a report"},
    }
    card = A.build_fallback_card(item)
    assert card["_fallback"] is True
    assert card["task"]
    assert card["input_schema"] and card["output_fields"]
    # Fallback cards are built after validation fails and are never re-validated, so the
    # bare source_path is honest here rather than a fabricated #L1-L1 span.
    assert card["provenance"]["paths"] == ["overbae/tasks/workshop.py"]
    assert {t["name"] for t in card["tool_spec"]} == {"sample_rows", "query_schema"}


def test_normalize_coerces_string_expected_output():
    card = _valid_card()
    card["expected_output"] = "a good answer is grounded and correct"
    norm = A.normalize_capability_card(card)
    assert norm["expected_output"]["description"] == "a good answer is grounded and correct"
    assert norm["expected_output"]["quality_signals"] == []


def test_write_and_load_bundle_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_capability_card(_valid_card())
    capabilities = [{"slug": "data-capability", "name": "Data Capability", "card": card}]

    result = A.write_codebase_bundle(
        "repo-1", capabilities, head_sha="abc123", repo_full_name="o/r"
    )
    bundle_dir = A.codebase_bundle_dir("repo-1")
    assert bundle_dir.is_dir()
    assert (bundle_dir / A.CAPABILITY_CARD_FILE).is_file()
    assert (bundle_dir / A.IO_SCHEMA_FILE).is_file()
    assert (bundle_dir / A.MANIFEST_FILE).is_file()

    raw = (bundle_dir / A.CAPABILITY_CARD_FILE).read_text(encoding="utf-8")
    assert json.loads(raw)["task"] == card["task"]

    manifest = result["manifest"]
    assert manifest["head_sha"] == "abc123"
    assert manifest["primary_capability_slug"] == "data-capability"
    kinds = {a["kind"] for a in manifest["artifacts"]}
    assert "capability_card" in kinds and "io_schema" in kinds

    loaded = A.load_codebase_bundle("repo-1", capability_slug="data-capability")
    assert loaded is not None
    assert loaded["card"]["task"] == card["task"]
    assert loaded["io_schema"]["input_schema"] == card["input_schema"]
    assert loaded["capability_slug"] == "data-capability"


def test_load_bundle_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    assert A.load_codebase_bundle("does-not-exist") is None


def test_write_empty_capabilities_noops(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    result = A.write_codebase_bundle("repo-x", [])
    assert result["manifest"] is None


def test_normalize_modes_tolerates_absence_and_shapes():
    assert A.normalize_modes(None) == []
    assert A.normalize_modes("nope") == []
    modes = A.normalize_modes(
        [
            {"name": "analysis", "entrypoint_fn": "run_analysis", "source_path": "w.py"},
            {"name": "fix", "prompt_builder": "build_fix_prompt"},
            "transform",
            {"junk": True},  # dropped: no name/entrypoint
        ]
    )
    assert [m["name"] for m in modes] == ["analysis", "fix", "transform"]
    assert modes[1]["prompt_builder"] == "build_fix_prompt"


def test_normalize_llm_utilities_tolerates_absence_and_shapes():
    assert A.normalize_llm_utilities(None) == []
    utils = A.normalize_llm_utilities(
        [
            {
                "name": "rubric_compiler",
                "purpose": "compile rubric",
                "called_by": "Eval Judge",
                "source_path": "overbae/eval/rubric.py",
                "provenance": {"paths": ["overbae/eval/rubric.py#L1-L20"]},
            },
            {"purpose": "no name → dropped"},
        ]
    )
    assert len(utils) == 1
    assert utils[0]["name"] == "rubric_compiler"
    assert utils[0]["provenance"]["paths"] == ["overbae/eval/rubric.py#L1-L20"]


def test_valid_card_without_modes_still_valid():
    card = _valid_card()
    assert "modes" not in card
    assert A.validate_capability_card(card) == []
    assert A.normalize_capability_card(card)["modes"] == []


def test_normalize_anchors_preserves_specialized_decorator_kinds():
    anchors = [
        {"qualname": f"m.{kind}", "kind": kind, "file": "m.py#L1-L2"}
        for kind in ("entry_point", "workflow", "tool", "retrieval", "function")
    ]

    assert [anchor["kind"] for anchor in A.normalize_anchors(anchors)] == [
        "entry_point",
        "workflow",
        "tool",
        "retrieval",
        "function",
    ]


def test_normalize_card_preserves_modes():
    card = _valid_card()
    card["modes"] = [{"name": "fix", "entrypoint_fn": "run_fix"}]
    modes = A.normalize_capability_card(card)["modes"]
    assert len(modes) == 1
    assert modes[0]["name"] == "fix"
    assert modes[0]["entrypoint_fn"] == "run_fix"
    assert modes[0]["source_path"] == ""
    assert modes[0]["purpose"] == ""
    assert modes[0]["routing"] == ""
    assert modes[0]["model"] == ""
    assert modes[0]["output"] == ""
    assert modes[0]["prompt_excerpt"] == ""


def test_fallback_card_includes_modes_from_item():
    item = {
        "name": "Workshop",
        "description": "runs lanes",
        "source_path": "overbae/tasks/workshop.py",
        "modes": [{"name": "analysis", "entrypoint_fn": "run_analysis"}],
    }
    card = A.build_fallback_card(item)
    assert [m["name"] for m in card["modes"]] == ["analysis"]
    assert card["provenance"]["paths"] == ["overbae/tasks/workshop.py"]


def test_bundle_captures_modes_and_llm_utilities(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_capability_card(
        {**_valid_card(), "modes": [{"name": "fix", "entrypoint_fn": "run_fix"}]}
    )
    capabilities = [{"slug": "ws", "name": "Workshop", "card": card}]
    utilities = [
        {
            "name": "rubric_compiler",
            "called_by": "Eval Judge",
            "source_path": "overbae/eval/rubric.py",
            "provenance": {"paths": ["overbae/eval/rubric.py#L1-L20"]},
        }
    ]
    result = A.write_codebase_bundle("repo-7", capabilities, llm_utilities=utilities)

    bundle_dir = A.codebase_bundle_dir("repo-7")
    assert (bundle_dir / A.LLM_UTILITIES_FILE).is_file()
    manifest = result["manifest"]
    assert manifest["llm_utilities"][0]["name"] == "rubric_compiler"
    kinds = {a["kind"] for a in manifest["artifacts"]}
    assert "llm_utility" in kinds

    loaded = A.load_codebase_bundle("repo-7", capability_slug="ws")
    assert [m["name"] for m in loaded["modes"]] == ["fix"]
    assert loaded["llm_utilities"][0]["name"] == "rubric_compiler"


def test_normalize_tool_spec_tolerates_absence_and_garbage():
    assert A.normalize_tool_spec(None) == []
    assert A.normalize_tool_spec("nope") == []
    assert A.normalize_tool_spec(["bare", {"purpose": "no name"}, 7]) == []


def test_normalize_tool_spec_full_shape_and_back_compat():
    tools = A.normalize_tool_spec(
        [
            {
                "name": "query_db",
                "purpose": "run a SQL query",
                "args": "sql, params",
                "side_effect": "read",
                "returns": "rows matching the query",
                "arguments": [
                    {"name": "sql", "type": "str", "required": True, "description": "the query"},
                    {"name": "params", "type": "list", "description": "bind params"},
                    "shorthand_arg",
                ],
                "integration": "Postgres",
                "provenance": ["overbae/db.py#L10-L40", "  ", "overbae/db.py#L80"],
            }
        ]
    )
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "query_db"
    assert tool["purpose"] == "run a SQL query"
    assert tool["args"] == "sql, params"
    assert tool["side_effect"] == "read"
    assert tool["returns"] == "rows matching the query"
    assert tool["integration"] == "Postgres"
    assert tool["provenance"] == ["overbae/db.py#L10-L40", "overbae/db.py#L80"]
    assert tool["arguments"][0] == {
        "name": "sql",
        "type": "str",
        "required": True,
        "description": "the query",
    }
    assert tool["arguments"][1]["required"] is False
    assert tool["arguments"][2] == {
        "name": "shorthand_arg",
        "type": "",
        "required": False,
        "description": "",
    }


def test_normalize_tool_spec_constrains_side_effect_with_fallback():
    for raw, expected in [
        ("WRITE", "write"),
        ("external", "external"),
        ("none", "none"),
        ("paid", "none"),  # not in the allowed set → fallback
        (None, "none"),
        (123, "none"),
    ]:
        tools = A.normalize_tool_spec([{"name": "t", "side_effect": raw}])
        assert tools[0]["side_effect"] == expected


def test_normalize_tool_spec_defaults_when_new_fields_absent():
    tools = A.normalize_tool_spec([{"name": "search", "purpose": "find", "args": "q"}])
    tool = tools[0]
    assert tool["side_effect"] == "none"
    assert tool["returns"] == ""
    assert tool["arguments"] == []
    assert tool["integration"] == ""
    assert tool["provenance"] == []


def test_normalize_capability_card_normalizes_tool_spec():
    card = _valid_card()
    card["tool_spec"] = [
        {"name": "search", "purpose": "retrieve evidence", "args": "query", "side_effect": "read"},
        {"junk": True},  # no name → dropped
    ]
    norm = A.normalize_capability_card(card)
    assert len(norm["tool_spec"]) == 1
    tool = norm["tool_spec"][0]
    assert tool["side_effect"] == "read"
    assert tool["arguments"] == []
    assert tool["integration"] == ""
    assert "provenance" in tool


def test_normalize_modes_extended_fields():
    modes = A.normalize_modes(
        [
            {
                "name": "fix",
                "entrypoint_fn": "run_fix",
                "purpose": "apply patches to failing rows",
                "routing": "selected when analysis finds fixable smells",
                "model": "composer-2.5",
                "output": "patches",
                "prompt_excerpt": "You are a fixer...",
            },
            "transform",  # bare string → empty descriptive defaults
        ]
    )
    fix, transform = modes
    assert fix["purpose"] == "apply patches to failing rows"
    assert fix["routing"] == "selected when analysis finds fixable smells"
    assert fix["model"] == "composer-2.5"
    assert fix["output"] == "patches"
    assert fix["prompt_excerpt"] == "You are a fixer..."
    assert transform["name"] == "transform"
    assert transform["purpose"] == "" and transform["output"] == ""


def test_normalize_llm_utilities_extended_fields():
    utils = A.normalize_llm_utilities(
        [
            {
                "name": "rubric_compiler",
                "purpose": "compile rubric",
                "called_by": "Eval Judge",
                "io_contract": "in: criteria -> out: rubric json",
                "cardinality": "per_candidate",
                "prompt_excerpt": "Compile the following criteria...",
                "structured_output": True,
            },
            {"name": "bare_util"},  # new fields default
        ]
    )
    rubric, bare = utils
    assert rubric["io_contract"] == "in: criteria -> out: rubric json"
    assert rubric["cardinality"] == "per_candidate"
    assert rubric["prompt_excerpt"] == "Compile the following criteria..."
    assert rubric["structured_output"] is True
    assert bare["io_contract"] == ""
    assert bare["cardinality"] == "unknown"
    assert bare["structured_output"] is False


def test_normalize_llm_utilities_constrains_cardinality_with_fallback():
    for raw, expected in [
        ("per_run", "per_run"),
        ("PER_ROW", "per_row"),
        ("per_candidate", "per_candidate"),
        ("hourly", "unknown"),  # not in the allowed set → fallback
        (None, "unknown"),
    ]:
        utils = A.normalize_llm_utilities([{"name": "u", "cardinality": raw}])
        assert utils[0]["cardinality"] == expected


def test_card_roundtrip_preserves_extended_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_capability_card(
        {
            **_valid_card(),
            "tool_spec": [
                {
                    "name": "write_file",
                    "purpose": "persist output",
                    "side_effect": "write",
                    "returns": "the written path",
                    "arguments": [{"name": "path", "type": "str", "required": True}],
                    "integration": "filesystem",
                    "provenance": ["overbae/io.py#L1-L9"],
                }
            ],
            "modes": [{"name": "fix", "entrypoint_fn": "run_fix", "output": "patches"}],
        }
    )
    capabilities = [{"slug": "ws", "name": "Workshop", "card": card}]
    utilities = [
        {
            "name": "rubric_compiler",
            "called_by": "Workshop",
            "io_contract": "in: criteria -> out: rubric",
            "cardinality": "per_run",
            "structured_output": True,
            "provenance": {"paths": ["overbae/eval/rubric.py#L1-L20"]},
        }
    ]
    A.write_codebase_bundle("repo-ext", capabilities, llm_utilities=utilities)

    loaded = A.load_codebase_bundle("repo-ext", capability_slug="ws")
    assert loaded is not None
    tool = loaded["tool_spec"][0]
    assert tool["side_effect"] == "write"
    assert tool["returns"] == "the written path"
    assert tool["arguments"][0]["name"] == "path"
    assert tool["integration"] == "filesystem"
    assert tool["provenance"] == ["overbae/io.py#L1-L9"]
    assert loaded["modes"][0]["output"] == "patches"
    util = loaded["llm_utilities"][0]
    assert util["io_contract"] == "in: criteria -> out: rubric"
    assert util["cardinality"] == "per_run"
    assert util["structured_output"] is True


_EMPTY_OUTPUT_SCHEMA = {"required_keys": [], "properties": {}, "provenance": []}


def test_normalize_output_schema_tolerates_absence_and_garbage():
    assert A.normalize_output_schema(None) == _EMPTY_OUTPUT_SCHEMA
    assert A.normalize_output_schema("nope") == _EMPTY_OUTPUT_SCHEMA
    assert A.normalize_output_schema([]) == _EMPTY_OUTPUT_SCHEMA
    assert A.normalize_output_schema({}) == _EMPTY_OUTPUT_SCHEMA
    garbled = A.normalize_output_schema(
        {"required_keys": "answer", "properties": ["not", "a", "dict"], "provenance": "x.py#L1"}
    )
    assert garbled == _EMPTY_OUTPUT_SCHEMA


def test_normalize_output_schema_well_formed_roundtrips():
    schema = A.normalize_output_schema(
        {
            "required_keys": ["answer", " reasoning ", ""],
            "properties": {"answer": "str, non-empty", "score": "float in [0,1]"},
            "provenance": ["overbae/capability.py#L10-L40", "  "],
        }
    )
    assert schema["required_keys"] == ["answer", "reasoning"]
    assert schema["properties"] == {"answer": "str, non-empty", "score": "float in [0,1]"}
    assert schema["provenance"] == ["overbae/capability.py#L10-L40"]


def test_normalize_constraints_tolerates_absence_and_garbage():
    assert A.normalize_constraints(None) == []
    assert A.normalize_constraints("nope") == []
    assert A.normalize_constraints(["bare", {"type": "budget"}, 7]) == [
        {
            "rule": "bare",
            "type": "output_format",
            "params": {},
            "provenance": [],
        }
    ]


def test_normalize_constraints_well_formed_and_type_fallback():
    constraints = A.normalize_constraints(
        [
            {
                "rule": "call report_stage exactly once per phase",
                "type": "tool_discipline",
                "params": {"tool": "report_stage", "max_per_phase": 1},
                "provenance": ["overbae/capability.py#L100-L120"],
            },
            {"rule": "at most 30 tool calls", "type": "BUDGET", "params": {"max_calls": 30}},
            {"rule": "weird type collapses", "type": "vibes"},
            {"rule": "missing fields default"},
        ]
    )
    assert len(constraints) == 4
    assert constraints[0] == {
        "rule": "call report_stage exactly once per phase",
        "type": "tool_discipline",
        "params": {"tool": "report_stage", "max_per_phase": 1},
        "provenance": ["overbae/capability.py#L100-L120"],
    }
    assert constraints[1]["type"] == "budget"
    assert constraints[1]["params"] == {"max_calls": 30}
    assert constraints[2]["type"] == "output_format"  # unknown type → fallback
    assert constraints[3] == {
        "rule": "missing fields default",
        "type": "output_format",
        "params": {},
        "provenance": [],
    }


def test_normalize_tool_protocol_tolerates_absence_and_garbage():
    assert A.normalize_tool_protocol(None) == []
    assert A.normalize_tool_protocol({"rule": "not a list"}) == []
    assert A.normalize_tool_protocol(["bare", {"kind": "ordering"}, 7]) == []


def test_normalize_tool_protocol_well_formed_and_kind_fallback():
    protocol = A.normalize_tool_protocol(
        [
            {
                "rule": "evidence must come from get_rows_by_id/sample_rows before citation",
                "tools": ["get_rows_by_id", " sample_rows ", ""],
                "kind": "evidence",
                "provenance": ["overbae/tools.py#L5-L30"],
            },
            {"rule": "weird kind collapses", "kind": "handshake", "tools": "not-a-list"},
        ]
    )
    assert len(protocol) == 2
    assert protocol[0]["tools"] == ["get_rows_by_id", "sample_rows"]
    assert protocol[0]["kind"] == "evidence"
    assert protocol[0]["provenance"] == ["overbae/tools.py#L5-L30"]
    assert protocol[1]["kind"] == "precondition"  # unknown kind → fallback
    assert protocol[1]["tools"] == []
    assert protocol[0]["params"] == {}
    assert protocol[1]["params"] == {}


def test_validate_card_lenient_without_contract_keys():
    card = _valid_card()
    assert "output_schema" not in card
    assert "constraints" not in card
    assert "tool_protocol" not in card
    assert A.validate_capability_card(card) == []


def test_normalize_card_defaults_contract_keys_when_absent():
    norm = A.normalize_capability_card(_valid_card())
    assert norm["output_schema"] == _EMPTY_OUTPUT_SCHEMA
    assert norm["constraints"] == []
    assert norm["tool_protocol"] == []


def test_normalize_card_preserves_contract_keys():
    card = {
        **_valid_card(),
        "output_schema": {
            "required_keys": ["answer"],
            "properties": {"answer": "str"},
            "provenance": ["overbae/capability.py#L10-L40"],
        },
        "constraints": [
            {
                "rule": "tool budget 30",
                "type": "budget",
                "params": {"max_calls": 30},
                "provenance": ["overbae/capability.py#L50"],
            },
        ],
        "tool_protocol": [
            {
                "rule": "search before answer",
                "tools": ["search"],
                "kind": "ordering",
                "params": {"max_calls": 1},
                "provenance": ["overbae/capability.py#L60-L70"],
            },
        ],
    }
    norm = A.normalize_capability_card(card)
    assert norm["output_schema"]["required_keys"] == ["answer"]
    assert norm["constraints"][0]["params"] == {"max_calls": 30}
    assert norm["tool_protocol"][0]["tools"] == ["search"]
    assert norm["tool_protocol"][0]["params"] == {"max_calls": 1}


def test_fallback_card_carries_empty_contract_defaults():
    card = A.build_fallback_card({"name": "X", "source_path": "overbae/x.py"})
    assert card["output_schema"] == _EMPTY_OUTPUT_SCHEMA
    assert card["constraints"] == []
    assert card["tool_protocol"] == []
    assert card["provenance"]["paths"] == ["overbae/x.py"]


def test_io_schema_artifact_carries_output_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_capability_card(
        {
            **_valid_card(),
            "output_schema": {
                "required_keys": ["answer"],
                "properties": {"answer": "str, non-empty"},
                "provenance": ["overbae/capability.py#L10-L40"],
            },
        }
    )
    result = A.write_codebase_bundle("repo-os", [{"slug": "ws", "name": "W", "card": card}])

    io_payload = json.loads((A.codebase_bundle_dir("repo-os") / A.IO_SCHEMA_FILE).read_text())
    assert io_payload["output_schema"]["required_keys"] == ["answer"]
    assert io_payload["input_schema"] == card["input_schema"]

    io_artifact = next(a for a in result["manifest"]["artifacts"] if a["kind"] == "io_schema")
    assert io_artifact["content"]["output_schema"]["properties"] == {"answer": "str, non-empty"}

    loaded = A.load_codebase_bundle("repo-os", capability_slug="ws")
    assert loaded["io_schema"]["output_schema"]["provenance"] == ["overbae/capability.py#L10-L40"]


def test_io_schema_defaults_output_schema_for_old_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = {
        k: v for k, v in A.normalize_capability_card(_valid_card()).items() if k != "output_schema"
    }
    A.write_codebase_bundle("repo-old", [{"slug": "ws", "name": "W", "card": card}])
    loaded = A.load_codebase_bundle("repo-old", capability_slug="ws")
    assert loaded["io_schema"]["output_schema"] == _EMPTY_OUTPUT_SCHEMA


def _valid_trajectory_map() -> list[dict]:
    return [
        {
            "id": "happy-path",
            "name": "Happy path",
            "routing": "classifier returns isInvoice=true",
            "sequence": ["classify", "extract", "assemble"],
            "tools": ["search"],
            "terminal": {"kind": "emits_record", "description": "returns the record"},
            "divergences": ["refusal"],
            "provenance": ["overbae/agent.py#L10-L40"],
        },
        {
            "id": "refusal",
            "routing": "classifier returns isInvoice=false",
            "sequence": ["classify", "return None"],
            "tools": [],
            "terminal": {"kind": "returns_empty", "description": ""},
            "divergences": ["happy-path", "nonexistent-sibling"],
            "provenance": ["overbae/agent.py#L50-L55"],
        },
    ]


def test_normalize_trajectory_map_tolerates_absence_and_garbage():
    assert A.normalize_trajectory_map(None) == []
    assert A.normalize_trajectory_map("nope") == []
    assert A.normalize_trajectory_map([42, "str", {"routing": "no id"}]) == []


def test_normalize_trajectory_map_well_formed_and_terminal_fallback():
    out = A.normalize_trajectory_map(_valid_trajectory_map())
    assert [p["id"] for p in out] == ["happy-path", "refusal"]
    assert out[0]["terminal"] == {"kind": "emits_record", "description": "returns the record"}
    assert out[0]["sequence"] == ["classify", "extract", "assemble"]
    # name defaults to id when absent; unknown divergence targets are dropped.
    assert out[1]["name"] == "refusal"
    assert out[1]["divergences"] == ["happy-path"]

    bad_terminal = [{"id": "x", "terminal": {"kind": "explodes"}}]
    assert A.normalize_trajectory_map(bad_terminal)[0]["terminal"]["kind"] == "emits_record"


def test_normalize_trajectory_map_is_uncapped():
    paths = [{"id": f"p{i}", "terminal": {"kind": "emits_record"}} for i in range(40)]
    assert len(A.normalize_trajectory_map(paths)) == 40


def test_normalize_capability_card_carries_trajectory_map_and_fallback_is_empty():
    card = _valid_card()
    card["trajectory_map"] = _valid_trajectory_map()
    normalized = A.normalize_capability_card(card)
    assert [p["id"] for p in normalized["trajectory_map"]] == ["happy-path", "refusal"]
    assert A.normalize_capability_card(_valid_card())["trajectory_map"] == []
    assert A.build_fallback_card({"name": "bare"})["trajectory_map"] == []


def test_validation_flags_malformed_trajectory_map_but_not_absence():
    assert A.validate_capability_card(_valid_card()) == []

    card = _valid_card()
    card["trajectory_map"] = "nope"
    assert any("trajectory_map" in e for e in A.validate_capability_card(card))

    card["trajectory_map"] = [{"routing": "no id at all"}]
    assert any("trajectory_map[0]" in e for e in A.validate_capability_card(card))

    card["trajectory_map"] = [{"id": "x", "terminal": {"kind": "explodes"}}]
    errors = A.validate_capability_card(card)
    assert any("terminal.kind" in e for e in errors)

    card["trajectory_map"] = _valid_trajectory_map()
    assert A.validate_capability_card(card) == []


_BACKBONE_TOOL_SPEC = [
    {"name": "Fetch-Data", "purpose": "pull rows", "args": ""},
    {"name": "post_update", "purpose": "write result", "args": ""},
]


def _backbone_trajectory_map() -> list[dict]:
    return [
        {
            "id": "answer-question",
            "name": "Answer platform question",
            "routing": "request is in scope",
            "sequence": [
                {"step": "check scope", "anchors": ["m.run"], "may_use": []},
                {
                    "step": "decide: gather evidence or answer",
                    "kind": "model_invocation",
                    "input": "user question + history + tool schemas",
                    "action": "decide whether to call tools or answer",
                    "output": "tool_calls -> dispatch; answer -> respond",
                    "may_use": [
                        {"tool": "Fetch-Data", "when": "the answer needs rows"},
                        {"tool": "undeclared_tool", "when": "tool_spec never declared it"},
                        {"tool": "fetch_data", "when": "duplicate after canonicalization"},
                    ],
                },
                "synthesize answer",
            ],
            "tools": ["post_update"],
            "terminal": {"kind": "emits_record", "description": "final answer"},
            "divergences": [],
            "provenance": ["overbae/agent.py#L1-L9"],
        }
    ]


def test_normalize_trajectory_steps_coerces_legacy_strings_to_bare_steps():
    sequence, steps = A.normalize_trajectory_steps(["classify", " extract ", ""], None)
    assert sequence == ["classify", "extract"]
    assert [s["step"] for s in steps] == ["classify", "extract"]
    assert all(s["kind"] == "agent_step" and s["anchors"] == [] for s in steps)


def test_normalize_trajectory_map_backbone_steps_and_may_use():
    (path,) = A.normalize_trajectory_map(_backbone_trajectory_map(), _BACKBONE_TOOL_SPEC)
    assert path["sequence"] == [
        "check scope",
        "decide: gather evidence or answer",
        "synthesize answer",
    ]
    assert path["steps"][0] == {
        "step": "check scope",
        "kind": "agent_step",
        "anchors": ["m.run"],
        "input": "",
        "action": "",
        "output": "",
        "may_use": [],
    }
    invocation = path["steps"][1]
    assert invocation["kind"] == "model_invocation"
    assert invocation["input"] == "user question + history + tool schemas"
    assert invocation["action"] == "decide whether to call tools or answer"
    assert invocation["output"] == "tool_calls -> dispatch; answer -> respond"
    # Undeclared tools are dropped; canonical duplicates dedupe to the first.
    assert invocation["may_use"] == [{"tool": "Fetch-Data", "when": "the answer needs rows"}]
    assert path["steps"][2]["step"] == "synthesize answer"
    assert path["steps"][2]["kind"] == "agent_step"
    assert path["tools"] == ["post_update", "Fetch-Data"]


def test_normalize_trajectory_steps_blanks_contract_on_agent_steps():
    _, steps = A.normalize_trajectory_steps(
        [
            {"step": "harness work", "kind": "agent_step", "input": "leak", "output": "leak"},
            {"step": "odd kind", "kind": "explodes", "action": "leak"},
        ],
        None,
    )
    assert steps[0]["input"] == "" and steps[0]["output"] == ""
    assert steps[1]["kind"] == "agent_step" and steps[1]["action"] == ""


def test_normalize_trajectory_map_is_idempotent_on_backbone_paths():
    once = A.normalize_trajectory_map(_backbone_trajectory_map(), _BACKBONE_TOOL_SPEC)
    twice = A.normalize_trajectory_map(once, _BACKBONE_TOOL_SPEC)
    assert twice == once
    assert twice[0]["steps"], "renormalizing must not drop the structured backbone"


def test_normalize_trajectory_map_without_tool_spec_keeps_may_use():
    (path,) = A.normalize_trajectory_map(_backbone_trajectory_map())
    assert [c["tool"] for c in path["steps"][1]["may_use"]] == [
        "Fetch-Data",
        "undeclared_tool",
    ]


def test_normalize_capability_card_filters_step_anchors_and_validates_may_use():
    card = _valid_card()
    card["tool_spec"] = _BACKBONE_TOOL_SPEC
    card["anchors"] = [{"qualname": "m.run", "kind": "entry_point", "file": "m.py#L1-L5"}]
    card["trajectory_map"] = _backbone_trajectory_map()
    card["trajectory_map"][0]["sequence"][0]["anchors"] = ["m.run", "m.fabricated"]
    (path,) = A.normalize_capability_card(card)["trajectory_map"]
    assert path["steps"][0]["anchors"] == ["m.run"]
    assert path["steps"][1]["may_use"] == [{"tool": "Fetch-Data", "when": "the answer needs rows"}]


def test_validation_accepts_both_sequence_formats_and_flags_garbage():
    card = _valid_card()
    card["trajectory_map"] = _valid_trajectory_map()
    assert A.validate_capability_card(card) == []

    card["trajectory_map"] = _backbone_trajectory_map()
    assert A.validate_capability_card(card) == []

    card["trajectory_map"][0]["sequence"] = [{"may_use": [], "anchors": []}]
    assert any("sequence" in e for e in A.validate_capability_card(card))
