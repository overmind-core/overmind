from pathlib import Path

from daytona import Image

from workshop_runtime.environment import FILES


def build_image(root):
    root = Path(root)
    image = Image.base("python:3.13-slim-bookworm")
    for local, remote in [
        ("docker/workshop/requirements.txt", "requirements.txt"),
        ("docker/workshop/build.sh", "build.sh"),
    ]:
        image = image.add_local_file(root / local, "/opt/overmind-runtime/" + remote)
    image = image.run_commands("sh /opt/overmind-runtime/build.sh")
    for relative in ["workshop_runtime/__init__.py", "modal_shared/__init__.py", *FILES]:
        image = image.add_local_file(root / relative, "/opt/overmind-runtime/lib/" + relative)
    return image.run_commands(
        "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.environment > /opt/overmind-runtime/runtime.json"
    ).env(
        {
            "PATH": "/opt/overmind-runtime/venv/bin:/usr/local/bin:/usr/bin:/bin",
            "MPLBACKEND": "Agg",
            "TIKTOKEN_CACHE_DIR": "/opt/overmind-runtime/tokenizers",
            "PIP_NO_INDEX": "1",
            "HF_HUB_OFFLINE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
