from overbae.services.systemone import parse


def test_choice_descriptions_reach_the_fine_tune_question():
    _model, _state, _names, decisions = parse(
        {
            "model": "ft-example",
            "state": "An email.",
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": "Which risk applies?",
                    "criteria": {
                        "low": "Little evidence of publisher risk.",
                        "high": None,
                    },
                }
            },
        }
    )
    question = decisions[0]["question"]
    assert decisions[0]["options"] == ["low", "high"]
    assert "low: Little evidence of publisher risk." in question
    assert "high:" not in question


def test_choice_descriptions_already_in_the_question_are_not_repeated():
    _model, _state, _names, decisions = parse(
        {
            "model": "ft-example",
            "state": "An email.",
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": "Which risk applies?\nlow: Little evidence of publisher risk.",
                    "criteria": {
                        "low": "Little evidence of publisher risk.",
                        "high": "Strong evidence of publisher risk.",
                    },
                }
            },
        }
    )
    question = decisions[0]["question"]
    assert question.count("low: Little evidence of publisher risk.") == 1
    assert "high: Strong evidence of publisher risk." in question
