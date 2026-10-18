import json
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.chassis import extract_chassis, reachable_names
from overmind.config import load
from overmind.utils import convert_json_to_toml

AGENT_SRC = textwrap.dedent(
    """
    import OpenAI from "openai";
    import { withRetry } from "./retry";

    export const SYSTEM_PROMPT = `You are a support triage agent.
    Answer in two sentences and cite the order id.`;

    const client = new OpenAI();

    export const runTurn = withRetry(
      async (userText: string) => {
        const context = assembleContext(userText);
        return emitAnswer(await callModel(context));
      },
      { attempts: 2 },
    );

    export const lookup = withRetry(async (id: string) => id, { attempts: 1 });

    async function callModel(messages: string[]) {
      return client.chat.completions.create({ model: "gpt-5-mini", messages: [] });
    }

    const assembleContext = (userText: string) => [SYSTEM_PROMPT, userText];

    function emitAnswer(reply: unknown) {
      return reply;
    }

    export function orphanHelper() {
      return null;
    }

    export class Router {
      route = async (text: string) => this.pick(text);
      pick(text: string) {
        return runTurn(text);
      }
    }
    """
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agent.ts").write_text(AGENT_SRC, encoding="utf-8")
    ignored = tmp_path / "node_modules" / "dep"
    ignored.mkdir(parents=True)
    (ignored / "index.js").write_text("export function leaked() {}", encoding="utf-8")
    return tmp_path


def _span(path: str, needle: str, length: int) -> str:
    lines = AGENT_SRC.splitlines()
    start = next(i for i, line in enumerate(lines, 1) if needle in line)
    return f"{path}#L{start}-L{start + length - 1}"


def test_capabilities_json_becomes_a_verified_toml_for_a_typescript_repo(tmp_path):
    _repo(tmp_path)
    src = tmp_path / "overmind_capabilities.json"
    src.write_text(
        json.dumps({
            "repo_summary": "demo",
            "capabilities": [
                {
                    "slug_hint": "triage",
                    "name": "Triage",
                    "system_prompt_span": _span("src/agent.ts", "export const SYSTEM_PROMPT", 2),
                    "capability_card": {
                        "task": "triage",
                        "anchors": [
                            {"qualname": "src.agent.runTurn", "kind": "entry_point", "file": "src/agent.ts"},
                            {"qualname": "src.agent.callModel", "kind": "function", "file": "src/agent.ts"},
                            {"qualname": "agent.Router.route", "kind": "function", "file": "src/agent.ts"},
                            {"qualname": "src.agent.orphanHelper", "kind": "function", "file": "src/agent.ts"},
                            {"qualname": "src.agent.doesNotExist", "kind": "function", "file": "src/agent.ts"},
                        ],
                        "trajectory_map": [
                            {"id": "handle-turn", "anchors": ["src.agent.runTurn", "src.agent.callModel"]},
                            {"id": "orphan", "anchors": ["src.agent.runTurn", "src.agent.orphanHelper"]},
                            {"id": "fabricated", "anchors": ["src.agent.doesNotExist"]},
                        ],
                    },
                    "eval_matrix": [{"name": "Quality", "type": "custom_judge", "rubric": "score"}],
                }
            ],
        })
    )
    dest = tmp_path / "overmind.toml"
    convert_json_to_toml(src, dest, repo_root=tmp_path)

    triage = load(dest).capabilities["triage"]
    assert triage.system_prompt == "You are a support triage agent.\nAnswer in two sentences and cite the order id."
    assert [a["qualname"] for a in triage.capability_card["anchors"]] == [
        "src.agent.runTurn",
        "src.agent.callModel",
        "agent.Router.route",
        "src.agent.orphanHelper",
    ]
    paths = {p["id"]: p for p in triage.capability_card["trajectory_map"]}
    assert paths["handle-turn"]["verified"] is True
    assert paths["orphan"]["verified"] is False
    assert paths["fabricated"]["verified"] is False


def test_call_graph_follows_arrow_functions_class_fields_and_wrapped_functions(tmp_path):
    chassis = extract_chassis(str(_repo(tmp_path)))
    assert {
        "src.agent.runTurn",
        "src.agent.callModel",
        "src.agent.assembleContext",
        "src.agent.Router.route",
        "src.agent.Router.pick",
    } <= set(chassis.functions)
    assert not any("leaked" in name for name in chassis.functions)
    assert reachable_names("src.agent.Router.route", chassis) >= {"pick", "runTurn", "callModel", "emitAnswer"}


def test_chassis_cli_selects_the_language(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "helper.py").write_text("def python_only():\n    return 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    detected = CliRunner().invoke(app, ["chassis", "--language", "ts"], catch_exceptions=False)
    assert detected.exit_code == 0, detected.output
    assert "src.agent.runTurn" in detected.output
    assert "python_only" not in detected.output

    python = CliRunner().invoke(app, ["chassis", "--language", "python"], catch_exceptions=False)
    assert "python_only" in python.output
    assert "runTurn" not in python.output

    unknown = CliRunner().invoke(app, ["chassis", "--language", "cobol"])
    assert unknown.exit_code != 0
