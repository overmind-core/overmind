import json
from pathlib import Path

SKILLS = {
    "decision-data": {
        "description": "Prepare and audit decision training or evaluation from observed labels, scores or distributions.",
        "consumers": ["decision_training", "decision_evaluation"],
    },
    "conversation-data": {
        "description": "Prepare and audit instruction, conversation and tool-use data for SFT or model evaluation.",
        "consumers": ["sft", "model_evaluation"],
    },
}


def catalog():
    return [
        {"name": name, "description": value["description"], "path": f"skills/{name}/SKILL.md"}
        for name, value in SKILLS.items()
    ]


def files(consumers):
    result = {}
    for name, specification in SKILLS.items():
        path = f"skills/{name}/SKILL.md"
        text = (Path(__file__).parent / path).read_text()
        contracts = {key: consumers[key] for key in [*specification["consumers"], "shared"]}
        result[path] = (
            text
            + "\n## Native consumer contracts\n\n```json\n"
            + json.dumps(contracts, ensure_ascii=False, separators=(",", ":"))
            + "\n```\n"
        )
    return result


def load(root, names):
    return {
        name: (Path(root) / "context" / f"skills/{name}/SKILL.md").read_text()
        for name in dict.fromkeys(names)
        if name in SKILLS
    }
