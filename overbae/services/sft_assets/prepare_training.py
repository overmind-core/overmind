import json
import sys
from pathlib import Path

from modal_shared.decisions import DECISION_OBJECTIVES

if __name__ == "__main__":
    request, output = Path(sys.argv[1]), Path(sys.argv[2])
    if json.loads(request.read_text()).get("objective") in DECISION_OBJECTIVES:
        from prepare_native_training import prepare

        prepare(request, output)
    else:
        from preprocess import run
        from pretok import pretok_row
        from transformers import AutoTokenizer

        run(request, output, AutoTokenizer.from_pretrained, pretok_row)
