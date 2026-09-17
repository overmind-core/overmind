from __future__ import annotations

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


def test_normalize_coerces_string_expected_output():
    card = _valid_card()
    card["expected_output"] = "a good answer is grounded and correct"
    norm = A.normalize_capability_card(card)
    assert norm["expected_output"]["description"] == "a good answer is grounded and correct"
    assert norm["expected_output"]["quality_signals"] == []


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


def test_card_without_modes_normalizes_to_empty_modes():
    card = _valid_card()
    assert "modes" not in card
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


def test_normalize_capability_card_carries_trajectory_map():
    card = _valid_card()
    card["trajectory_map"] = _valid_trajectory_map()
    normalized = A.normalize_capability_card(card)
    assert [p["id"] for p in normalized["trajectory_map"]] == ["happy-path", "refusal"]
    assert A.normalize_capability_card(_valid_card())["trajectory_map"] == []


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
