from dataclasses import asdict

from modal_shared.decisions import TARGET_FIELDS, decision_line, decision_request

RENDERER = "unsloth_clef"


def question_schema(request):
    decision_request(request)
    kind, options = request["kind"], request["options"]
    question = {"type": kind, "instructions": request["question"]}
    if kind == "choice":
        question["criteria"] = dict.fromkeys(options)
        order = sorted(range(len(options)), key=options.__getitem__)
    elif kind == "noul":
        question["criteria"] = {"false": options[0], "true": options[1]}
        order = [1, 0]
    else:
        question["criteria"] = options
        order = list(range(len(options)))
    return question, order


class DecisionEncoder:
    def __init__(self, tokenizer, encode_record):
        self.tokenizer, self.encode_record = tokenizer, encode_record

    def request(self, request):
        question, order = question_schema(request)
        # Encode the complete input once. Context admission must never silently truncate state.
        record = self.encode_record(
            self.tokenizer,
            {"state": request["state"], "questions": {"decision": question}},
            max_length=2**31 - 1,
        )
        encoded = asdict(record)
        ids = encoded.pop("input_ids")
        return {
            "input_ids": list(ids),
            "record": encoded,
            "option_order": order,
            "kind": request["kind"],
            "question": request["question"],
            "options": request["options"],
        }

    def training_row(self, row):
        value = decision_line(row)["decision"]
        request = {key: value[key] for key in ("state", "question", "kind", "options")}
        return {
            **self.request(request),
            **{key: value[key] for key in TARGET_FIELDS if key in value},
            "weight": value.get("weight", 1.0),
            **({"group": row["group"]} if "group" in row else {}),
        }
