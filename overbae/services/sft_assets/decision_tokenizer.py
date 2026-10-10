import hashlib
import importlib.util
import sys
from importlib.metadata import distribution


def load_encoder():
    package = distribution("unsloth")
    path = package.locate_file("unsloth/_vendor/clef/joint_schema_model.py")
    if (
        package.version != "2026.10.3"
        or hashlib.sha256(path.read_bytes()).hexdigest()
        != "0e304cf7c6500e8bb59bef7e2afd2c6373f82596dfb3b57d1aa93c175e2dc3a3"
    ):
        raise ValueError("Decision preprocessing requires the qualified Unsloth encoder")
    # Unsloth's package initializer requires a GPU. Load its unchanged, pinned
    # reference encoder directly so preparation can run on the CPU worker.
    spec = importlib.util.spec_from_file_location("overmind_clef_encoding", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.encode_record


encode_record = load_encoder()
