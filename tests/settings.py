"""Django settings for pytest — SQLite (in-memory) so CI/local tests never touch Postgres."""

from __future__ import annotations

import os

# Parent settings require these at import; stub for hermetic CI.
os.environ.setdefault("AWS_BUCKET_NAME", "test-ft-bucket")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("INFERENCE_API_URL", "http://inference.test")
os.environ.setdefault("INFERENCE_API_KEY", "testing")
os.environ.setdefault("FINETUNING_BACKEND", "modal")
os.environ.setdefault("HF_TOKEN", "testing")
os.environ.setdefault("BASETEN_API_KEY", "testing")
os.environ.setdefault("TOGETHER_API_KEY", "testing")
os.environ.setdefault("MODAL_TOKEN_ID", "testing")
os.environ.setdefault("MODAL_TOKEN_SECRET", "testing")

from overbae.settings import *  # noqa: E402, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Keep tests hermetic — never export Langfuse capability telemetry from the suite
# (the parent settings may have picked up real keys from a local .env).
LANGFUSE_PUBLIC_KEY = ""
LANGFUSE_SECRET_KEY = ""

# Remaining-credit billing on unless a test clears the key.
STRIPE_SECRET_KEY = "sk_test_billing"

# Local .env often points Celery at docker Redis, and an unmocked `.delay()` burns
# ~20s on connect timeout. Celery reads CELERY_BROKER_URL from os.environ above app
# config, so the env copy is overwritten too. In-memory transport accepts publishes
# without running task bodies (no ALWAYS_EAGER).
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

# Keep the query-embedding cache off Redis in tests.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# PBKDF2 makes every create_user ~80ms; MD5 is fine for hermetic unit tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
