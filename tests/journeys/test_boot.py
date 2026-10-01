import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from django.conf import settings

ROOT = Path(__file__).resolve().parents[2]

FILLED = {
    "HF_TOKEN": "hf_fake",
    "AWS_BUCKET_NAME": "checkpoints",
    "AWS_ACCESS_KEY_ID": "AKIAFAKE",
    "AWS_SECRET_ACCESS_KEY": "fake",
    "MODAL_TOKEN_ID": "ak-fake",
    "MODAL_TOKEN_SECRET": "as-fake",
    "INFERENCE_API_KEY": "fake",
    "OPENROUTER_API_KEY": "sk-or-fake",
}
HOSTED = {
    "DJANGO_DEBUG": "False",
    "DJANGO_SECRET_KEY": "a" * 50,
    "FIELD_ENCRYPTION_KEY": "Ysb0VvVb7yU0B8n7uQ5sN1tWm0bJvN4wqk2m2gIcY8w=",
    "CLERK_API_SECRET_KEY": "sk_test_fake",
    "STRIPE_SECRET_KEY": "sk_test_fake",
    "STRIPE_WEBHOOK_SECRET": "whsec_fake",
}


def _example() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _environment(**overrides: str) -> dict[str, str]:
    database = settings.DATABASES["default"]
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/tmp"),
        **_example(),
        **FILLED,
        "POSTGRES_DB": database["NAME"],
        "POSTGRES_USER": database["USER"],
        "POSTGRES_PASSWORD": database["PASSWORD"],
        "POSTGRES_HOST": database["HOST"] or "localhost",
        "POSTGRES_PORT": str(database["PORT"] or 5432),
        "CELERY_BROKER_URL": os.environ["TEST_REDIS_URL"],
        "CELERY_RESULT_BACKEND": os.environ["TEST_REDIS_URL"],
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "localhost,127.0.0.1",
        "DJANGO_SETTINGS_MODULE": "overbae.settings",
        **overrides,
    }
    return {key: value for key, value in env.items() if value is not None}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(env: dict[str, str]) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "overbae.asgi:application", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AssertionError(f"server exited {server.returncode}:\n{server.stdout.read()}")
        try:
            requests.get(f"{url}/health", timeout=2)
            return server, url
        except requests.ConnectionError:
            time.sleep(0.2)
    server.kill()
    raise AssertionError(f"server never listened:\n{server.stdout.read()}")


def _health(env: dict[str, str]) -> requests.Response:
    server, url = _serve(env)
    try:
        return requests.get(f"{url}/health", timeout=10)
    finally:
        server.terminate()
        server.wait(timeout=10)


def _setup(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup(); import overbae.asgi"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_self_hosted_environment_boots_and_answers_health():
    response = _health(_environment())
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "healthy"}


def test_the_hosted_environment_boots():
    result = _setup(_environment(**HOSTED))
    assert result.returncode == 0, result.stderr


def test_health_fails_when_the_database_is_unreachable():
    response = _health(_environment(POSTGRES_PORT=str(_free_port())))
    assert response.status_code >= 500


@pytest.mark.parametrize(
    ("blank", "named"),
    [
        ("HF_TOKEN", "HF_TOKEN"),
        ("AWS_BUCKET_NAME", "AWS_BUCKET_NAME"),
        ("MODAL_TOKEN_SECRET", "MODAL_TOKEN_SECRET"),
        ("INFERENCE_API_URL", "INFERENCE_API_URL"),
        ("INFERENCE_API_KEY", "INFERENCE_API_KEY"),
        ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    ],
)
def test_a_missing_required_variable_stops_startup_and_is_named(blank, named):
    result = _setup(_environment(**{blank: ""}))
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert named in result.stderr.strip().splitlines()[-1]


STORAGE = (
    "import django; django.setup(); from django.core.files.storage import storages; "
    "s = storages[{alias!r}]; "
    "print(type(s).__name__, getattr(s, 'bucket_name', '-'), "
    "getattr(s, 'session_profile', '-'), bool(getattr(s, 'access_key', '')))"
)


@pytest.mark.parametrize(
    ("alias", "bucket", "expected"),
    [
        ("default", {}, "FileSystemStorage - - False"),
        (
            "default",
            {"AWS_STORAGE_BUCKET_NAME": "hosted-media"},
            "S3Storage hosted-media administrator False",
        ),
        (
            "staticfiles",
            {"AWS_STATIC_BUCKET_NAME": "hosted-static"},
            "S3StaticStorage hosted-static administrator False",
        ),
    ],
    ids=["self-hosted files stay local", "hosted media", "hosted static"],
)
def test_file_storage_follows_the_bucket_settings_and_never_borrows_checkpoint_keys(
    alias, bucket, expected
):
    result = subprocess.run(
        [sys.executable, "-c", STORAGE.format(alias=alias)],
        cwd=ROOT,
        env=_environment(**bucket),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
