import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from sandbox import execute, packages
from sandbox.protocol import HeaderReader, header_bytes


def run_package(sandbox_server, code, *, limits=None, runtime=None, files=None):
    url, token = sandbox_server
    package = json.dumps(files or {"step.py": code}).encode()
    source = b'{"source_row": 0, "value": 9007199254740993}\n'
    header = {
        "mode": "package",
        "runtime": runtime or packages.runtime_id(),
        "entrypoint": "step.py",
        "parameters": {},
        "package_bytes": len(package),
        "input_bytes": len(source),
        "limits": limits or {"seconds": 20},
    }
    response = httpx.post(
        url + "/v1/run",
        content=header_bytes(header) + package + source,
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    if response.status_code != 200:
        return response.status_code, response.json(), b""
    reader = HeaderReader()
    payload = reader.feed(response.content)
    while reader.header and "progress" in reader.header:
        reader = HeaderReader()
        payload = reader.feed(payload)
    return response.status_code, reader.finish(), payload


isolated = pytest.mark.skipif(
    sys.platform != "darwin" and os.environ.get("SANDBOX_CONTAINER_TESTS") != "1",
    reason="Requires the root sandbox container on an internal network",
)


COPY = "import pathlib,sys\npathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())\n"


def test_package_runs_on_isolated_service_with_exact_rows_and_nested_modules(sandbox_server):
    code = "from nested.helper import copy\ncopy()\n"
    helper = "def copy():\n " + COPY.replace("\n", "\n ").rstrip() + "\n"
    status, result, payload = run_package(
        sandbox_server, code, files={"step.py": code, "nested/helper.py": helper}
    )
    assert status == 200 and result["ok"], result
    assert result["exit_code"] == 0
    assert json.loads(payload)["value"] == 9007199254740993


def test_runtime_mismatch_never_executes(sandbox_server):
    status, result, _ = run_package(
        sandbox_server, "raise Exception('executed')", runtime="sha256:" + "0" * 64
    )
    assert status == 400
    assert "pinned sandbox runtime" in result["error"]


@isolated
def test_restored_libraries_and_tokenizers_work_without_network(sandbox_server):
    code = """import importlib
for name in ('pandas', 'numpy', 'pyarrow', 'duckdb', 'scipy', 'sklearn', 'rapidfuzz',
             'regex', 'jsonschema', 'tiktoken', 'langdetect', 'datasketch', 'dateutil',
             'pytz', 'yaml', 'unidecode', 'ftfy', 'simhash', 'emoji', 'langcodes',
             'jellyfish', 'Levenshtein', 'markdownify', 'bs4', 'lxml', 'wordfreq'):
    importlib.import_module(name)
import tiktoken
assert tiktoken.get_encoding('cl100k_base').encode('Offline tokenization')
assert tiktoken.get_encoding('o200k_base').encode('Offline tokenization')
"""
    _, result, _ = run_package(sandbox_server, code + COPY, limits={"seconds": 60})
    assert result["ok"], result


def test_nonzero_exit_cannot_claim_success_in_a_status_file(sandbox_server):
    status, result, payload = run_package(
        sandbox_server,
        COPY + "import json\nopen('status.json','w').write('{\"ok\":true}')\nraise SystemExit(7)\n",
    )
    assert status == 200 and not result["ok"]
    assert result["exit_code"] == 7
    assert payload == b""


@pytest.mark.parametrize(
    "make",
    [
        "os.symlink('/etc/passwd', sys.argv[2])",
        "os.mkfifo(sys.argv[2])",
        "pathlib.Path('other').write_text('x'); os.link('other', sys.argv[2])",
    ],
)
def test_unsafe_output_is_rejected_without_blocking(sandbox_server, make):
    _, result, payload = run_package(sandbox_server, "import os,sys,pathlib\n" + make)
    assert not result["ok"]
    assert "readable regular" in result["error"]
    assert payload == b""


@isolated
def test_child_has_no_credentials_capabilities_or_network_route(sandbox_server):
    code = """import os,pathlib,socket
assert not any(k in os.environ for k in ('SANDBOX_TOKEN','DATABASE_URL','AWS_SECRET_ACCESS_KEY','OPENROUTER_API_KEY'))
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/data/media').exists()
for line in pathlib.Path('/proc/self/status').read_text().splitlines():
    if line.startswith(('CapEff:', 'CapPrm:', 'CapAmb:')):
        assert int(line.split()[1], 16) == 0
for path in ('/proc/1/environ', '/tmp/sandbox/ledger.json'):
    try:
        pathlib.Path(path).read_bytes()
    except PermissionError:
        pass
    else:
        raise AssertionError(path)
probe = socket.socket()
probe.settimeout(1)
assert probe.connect_ex(('1.1.1.1', 443)) != 0
probe.close()
"""
    _, result, _ = run_package(sandbox_server, code + COPY)
    assert result["ok"], result


@isolated
def test_concurrent_runs_have_distinct_uids_and_private_scratch(sandbox_server):
    code = """import os,pathlib,time
try:
    list(pathlib.Path('/tmp/sandbox').iterdir())
except PermissionError:
    pass
else:
    raise AssertionError('Another run is visible')
print(os.getuid())
time.sleep(1)
"""
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run_package(sandbox_server, code + COPY), range(2)))
    assert all(result[1]["ok"] for result in results), results
    assert len({result[1]["stdout"] for result in results}) == 2


@isolated
def test_detached_children_are_killed_before_completion(sandbox_server):
    script = """import os,time
if os.fork() == 0:
    os.setsid()
    time.sleep(60)
    os._exit(0)
time.sleep(.3)
"""
    _, result, _ = run_package(sandbox_server, script + COPY)
    assert result["ok"], result
    probe = """import os,pathlib
for entry in pathlib.Path('/proc').iterdir():
    if entry.name.isdigit():
        try:
            owner = entry.stat().st_uid
            state = (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0]
        except OSError:
            continue
        assert not (20000 <= owner < 21000 and owner != os.getuid() and state not in ('Z','X'))
"""
    _, result, _ = run_package(sandbox_server, probe + COPY)
    assert result["ok"], result


@pytest.mark.parametrize(
    "opening",
    [
        "fd=os.memfd_create('held'); write=lambda b: os.write(fd,b)",
        "f=open('held','wb'); os.unlink('held'); write=f.write",
    ],
)
@isolated
def test_unlinked_scratch_is_included_in_memory_accounting(sandbox_server, opening):
    code = (
        "import os,time\n"
        + opening
        + "\nblock=b'x'*(64*1024**2)\nfor i in range(32):\n write(block)\n time.sleep(.1)\ntime.sleep(3)\n"
    )
    _, result, _ = run_package(sandbox_server, code, limits={"seconds": 20, "memory_mb": 1024})
    assert not result["ok"], result
    assert "memory and scratch" in result["error"]


def test_resource_admission_retains_measured_expansion_and_rejects_small_caps():
    header = {
        "mode": "package",
        "input_bytes": 300 * 1024**2,
        "package_bytes": 1000,
        "limits": {"memory_mb": 512},
    }
    need = execute.need_for(header)
    assert need == int(header["input_bytes"] * 5.5) + 256 * 1024**2 + 1000
    assert "increase memory_mb" in execute.refusal(header, need, 8 * 1024**3)
