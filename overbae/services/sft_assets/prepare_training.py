import sys
from pathlib import Path

from preprocess import run
from pretok import pretok_row
from transformers import AutoTokenizer

if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]), AutoTokenizer.from_pretrained, pretok_row)
