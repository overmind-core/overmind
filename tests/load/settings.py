import copy
import os

from tests.settings import *  # noqa: F403

DATABASES = copy.deepcopy(DATABASES)  # noqa: F405
DATABASES["default"]["TEST"] = {"NAME": os.environ["OVERMIND_LOAD_TEST_DATABASE"]}
