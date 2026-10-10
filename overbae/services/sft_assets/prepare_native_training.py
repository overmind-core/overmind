from decision_tokenizer import encode_record
from preprocess import run
from transformers import AutoTokenizer


def prepare(request, output):
    run(request, output, AutoTokenizer.from_pretrained, None, decision_encode=encode_record)
