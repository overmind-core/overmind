import copy
import string
from types import SimpleNamespace

import pytest

from modal_shared import decisions


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return messages[0]["content"] + "<decision>"

    def encode(self, text, **kwargs):
        prefix, suffix = text.split("<decision>", 1)
        return [ord(c) + 20 for c in prefix] + [3] + ([{"A": 4, "B": 5}[suffix]] if suffix else [])


def request():
    return {
        "state": "coin evidence",
        "question": "Choose",
        "kind": "choice",
        "options": ["Heads", "Tails"],
    }


def test_input_only_inference_matches_training_tokens_without_references():
    book = [{"code": "A", "token_id": 4}, {"code": "B", "token_id": 5}]
    value = request()
    encoder = decisions.DecisionTokenizer(Tokenizer(), book)
    result = encoder.request(value)
    assert set(result) == {"input_ids", "option_token_ids", "kind"}
    assert decisions.decision_request(value) == value
    for q in ([0.2, 0.8], [1, 0]):
        trained = encoder.training_row({"decision": {**value, "target_probabilities": q}})
        assert result == {k: trained[k] for k in result}


@pytest.mark.parametrize("field", ["target_probabilities", "weight", "source", "expected_output"])
def test_input_contract_rejects_accidentally_passed_gold_or_metadata(field):
    value = {**request(), field: "must not reach the model"}
    with pytest.raises(ValueError):
        decisions.decision_request(value)


def test_codebook_does_not_assign_two_outcomes_to_one_token():
    codes = list(string.ascii_uppercase) + [
        a + b for a in string.ascii_uppercase for b in string.ascii_uppercase
    ]
    codes = codes[:255]

    class Aliased:
        def get_vocab(self):
            return {code: i for i, code in enumerate(codes)}

        def encode(self, text, **kwargs):
            return [0 if text in {"A", "B"} else codes.index(text)]

    with pytest.raises(ValueError, match="255 distinct"):
        decisions.codebook(Aliased())


def test_inference_rejects_aliased_saved_codebook():
    book = [{"code": "A", "token_id": 4}, {"code": "B", "token_id": 4}]
    with pytest.raises(ValueError):
        decisions.DecisionTokenizer(Tokenizer(), book).request(copy.deepcopy(request()))


class BPE:
    dropout = None


class IsolatedTokenizer(Tokenizer):
    def __init__(self):
        self.backend_tokenizer = SimpleNamespace(
            model=BPE(),
            normalizer=None,
            post_processor=None,
            truncation=None,
            padding=None,
            encode_special_tokens=False,
        )
        self.added_tokens_decoder = {
            3: SimpleNamespace(
                content="<decision>",
                special=True,
                normalized=False,
                single_word=False,
                lstrip=False,
                rstrip=False,
            ),
        }
        self.calls = []

    def encode(self, text, **kwargs):
        self.calls.append(text)
        return super().encode(text, **kwargs)


def test_isolated_boundary_checks_do_not_retokenize_repeated_long_prefixes():
    tokenizer = IsolatedTokenizer()
    encoder = decisions.DecisionTokenizer(
        tokenizer, [{"code": "A", "token_id": 4}, {"code": "B", "token_id": 5}]
    )
    for state in ["evidence " * 2000, "different " * 3000]:
        value = {**request(), "state": state}
        actual = encoder.request(value)
        expected = decisions.DecisionTokenizer(Tokenizer(), encoder.book).request(value)
        assert actual == expected
    assert len([text for text in tokenizer.calls if len(text) > 1000]) == 2


@pytest.mark.parametrize("unsafe", ["nested_added_token", "split_special", "dropout"])
def test_unproven_boundaries_keep_full_prompt_checks(unsafe):
    tokenizer = IsolatedTokenizer()
    if unsafe == "nested_added_token":
        tokenizer.added_tokens_decoder[8] = SimpleNamespace(
            content="prefix<decision>A",
            special=False,
            normalized=False,
            single_word=False,
            lstrip=False,
            rstrip=False,
        )
    elif unsafe == "split_special":
        tokenizer.backend_tokenizer.encode_special_tokens = True
    else:
        tokenizer.backend_tokenizer.model.dropout = 0.1
    encoder = decisions.DecisionTokenizer(
        tokenizer, [{"code": "A", "token_id": 4}, {"code": "B", "token_id": 5}]
    )
    encoder.request({**request(), "state": "evidence " * 2000})
    assert len([text for text in tokenizer.calls if len(text) > 1000]) == 3
