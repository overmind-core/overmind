from __future__ import annotations

from modal_shared.serving.merge import install_weight_fold


def main() -> None:
    # The streamer binds the weight iterator at import, so the fold is installed first.
    install_weight_fold()
    from vllm.entrypoints.cli.main import main as vllm_main

    vllm_main()


if __name__ == "__main__":
    main()
