import copy
import os

from tests.settings import *  # noqa: F403

DATABASES = copy.deepcopy(DATABASES)  # noqa: F405
CACHES = copy.deepcopy(CACHES)  # noqa: F405
CACHES["default"]["KEY_PREFIX"] = os.environ.get(
    "OVERMIND_LOAD_WORKER_DATABASE", os.environ["OVERMIND_LOAD_TEST_DATABASE"]
)
DATABASES["default"]["TEST"] = {"NAME": os.environ["OVERMIND_LOAD_TEST_DATABASE"]}
if os.environ.get("OVERMIND_LOAD_WORKER_DATABASE"):
    DATABASES["default"]["NAME"] = os.environ["OVERMIND_LOAD_WORKER_DATABASE"]
if os.environ.get("OVERMIND_LOAD_MEDIA_ROOT"):
    MEDIA_ROOT = os.environ["OVERMIND_LOAD_MEDIA_ROOT"]
