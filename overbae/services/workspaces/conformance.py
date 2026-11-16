"""What every workspace provider must do. The test suite runs these against the native
provider; ``manage.py check_workspaces`` runs them against the configured one, which is
the only way to check a hosted provider."""

import io
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from overbae.services.workspaces import Execution, Provider


@contextmanager
def _kernel(provider: Provider, project: str | None = None) -> Iterator[Callable[..., Execution]]:
    """A kernel in a new project's workspace, or in ``project``'s; a new workspace is
    removed afterwards."""
    owned = project is None
    project = project or uuid.uuid4().hex
    conversation = uuid.uuid4().hex

    def run(code: str, *, timeout: int = 60) -> Execution:
        return provider.run(project, conversation, code, timeout)

    run.project, run.conversation = project, conversation
    try:
        yield run
    finally:
        provider.stop(project, conversation)
        if owned:
            provider.remove(project)


def variables_persist_and_the_last_expression_is_shown(provider):
    with _kernel(provider) as run:
        first = run("rows = [1, 2, 3]")
        assert first.ok and first.output == "" and first.restarted is None, first
        result = run("print('total')\nsum(rows)")
        assert result.ok and result.output == "total\n6\n", result


def an_exception_returns_its_traceback_and_keeps_the_variables(provider):
    with _kernel(provider) as run:
        run("kept = 41")
        failed = run("def broken():\n    return 1 / 0\nbroken()")
        assert not failed.ok, failed
        assert "ZeroDivisionError" in failed.error and "return 1 / 0" in failed.error, failed
        assert run("kept + 1").output == "42\n"


def data_is_copied_once_and_read_only(provider):
    content = b"\x00overmind\xff" * 1000
    with _kernel(provider) as run:
        name = f"{uuid.uuid4().hex}.bin"
        assert not provider.has_data(run.project, name)
        provider.put_data(run.project, name, io.BytesIO(content))
        provider.put_data(run.project, name, io.BytesIO(b"replaced"))
        assert provider.has_data(run.project, name)
        result = run(
            f"data = open('data/{name}', 'rb').read()\n"
            "open('copy.bin', 'wb').write(data[::-1])\nlen(data)"
        )
        assert result.output == "10000\n", result
        assert provider.read_file(run.project, run.conversation, "copy.bin") == content[::-1]
        kept = run(
            "try:\n"
            f"    open('data/{name}', 'ab')\n"
            "except OSError:\n"
            "    kept = True\n"
            "else:\n"
            "    kept = False\n"
            "kept"
        )
        assert kept.output == "True\n", kept
        try:
            provider.read_file(run.project, run.conversation, "absent.bin")
        except FileNotFoundError:
            return
        raise AssertionError("A missing file was returned.")


def conversations_of_a_project_share_data_and_not_variables(provider):
    with _kernel(provider) as first, _kernel(provider, first.project) as second:
        name = f"{uuid.uuid4().hex}.txt"
        provider.put_data(first.project, name, io.BytesIO(b"shared"))
        first("mine = 1\nopen('own.txt', 'w').write('own')")
        assert second(f"open('data/{name}').read()").output == "'shared'\n"
        missing = second("mine")
        assert not missing.ok and "NameError" in missing.error, missing
        alone = second("import os\nos.path.exists('own.txt')")
        assert alone.output == "False\n", alone


# Every pinned library, used the way a kernel uses it: a library that cannot reach a file it
# needs under the kernel's confinement may abort the whole process, not raise.
LIBRARY_USE = """\
import multiprocessing, subprocess, sys, tempfile
import bs4, datasketch, dateutil.parser, duckdb, emoji, ftfy, jellyfish, jsonschema
import langcodes, langdetect, Levenshtein, lxml.etree, markdownify, matplotlib, numpy, pandas
import PIL.Image, pyarrow.parquet, pytz, rapidfuzz.fuzz, regex, scipy.stats, simhash
import sklearn.ensemble, unidecode, wordfreq, yaml
matplotlib.use("Agg")
import matplotlib.pyplot as plt

frame = pandas.DataFrame({"x": numpy.arange(200) % 7, "y": numpy.arange(200) % 2})
frame.to_parquet("check.parquet")
assert len(pandas.read_parquet("check.parquet")) == pyarrow.parquet.read_table("check.parquet").num_rows
database = duckdb.connect("check.duckdb")
database.register("frame", frame)
database.execute("create table kept as select * from frame order by x")
assert database.execute("select count(*) from kept").fetchone()[0] == 200
database.close()
sklearn.ensemble.RandomForestClassifier(n_estimators=8, n_jobs=2).fit(frame[["x"]], frame["y"])
scipy.stats.pearsonr(frame["x"], frame["y"])
plt.plot(frame["x"])
plt.title("check")
plt.savefig("check.png")
PIL.Image.open("check.png").verify()
assert markdownify.markdownify(str(bs4.BeautifulSoup("<b>x</b>", "lxml")))
lxml.etree.fromstring("<a><b/></a>")
yaml.safe_load("a: 1")
regex.findall(r"\\p{L}+", "wörld")
rapidfuzz.fuzz.ratio("ab", "ac"), jellyfish.jaro_similarity("ab", "ac"), Levenshtein.distance("ab", "ac")
ftfy.fix_text("wÃ¶rld"), unidecode.unidecode("wörld"), emoji.demojize("\\U0001F44D")
langcodes.standardize_tag("en-us"), langdetect.detect("This is an English sentence.")
wordfreq.word_frequency("the", "en"), simhash.Simhash("some text").value
datasketch.MinHash().update(b"a")
jsonschema.validate({"a": 1}, {"type": "object"})
dateutil.parser.parse("2026-01-02"), pytz.timezone("Europe/London")
with multiprocessing.Pool(2) as pool:
    assert pool.map(abs, [-1, -2]) == [1, 2]
child = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, text=True)
assert child.stdout == "1\\n", child
tempfile.NamedTemporaryFile().close()
"""


def the_pinned_libraries_work(provider):
    with _kernel(provider) as run:
        result = run(LIBRARY_USE, timeout=300)
        assert result.ok, result


def a_cell_past_its_deadline_is_interrupted_and_the_variables_stay(provider):
    with _kernel(provider) as run:
        run("marker = 'alive'")
        slow = run("while True:\n    pass", timeout=1)
        assert not slow.ok and "interrupted" in slow.error, slow
        assert run("marker").output == "'alive'\n"


def long_output_is_bounded_and_says_so(provider):
    with _kernel(provider) as run:
        result = run("print('x' * 5_000_000)")
        assert result.truncated and len(result.output) < 70_000, result.truncated
        assert "characters omitted" in result.output


def a_stopped_kernel_restarts_with_its_files(provider):
    with _kernel(provider) as run:
        run("gone = 1\nopen('kept.txt', 'w').write('kept')")
        provider.stop(run.project, run.conversation)
        provider.stop(run.project, run.conversation)
        after = run("open('kept.txt').read()")
        assert after.ok and after.output == "'kept'\n" and after.restarted, after
        assert not run("gone").ok


def a_kernel_that_dies_is_reported_and_replaced(provider):
    with _kernel(provider) as run:
        died = run("import os\nos._exit(1)")
        assert not died.ok and "kernel ended" in died.error, died
        again = run("1 + 1")
        assert again.ok and again.output == "2\n" and again.restarted, again


def a_project_sees_no_data_or_process_of_another(provider):
    marker = uuid.uuid4().hex
    with _kernel(provider) as first, _kernel(provider) as second:
        provider.put_data(first.project, f"{marker}.txt", io.BytesIO(b"secret"))
        started = first(
            "import subprocess, sys\n"
            f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', '{marker}'])"
        )
        assert started.ok, started
        seen = second(
            "import glob\n"
            "found = []\n"
            "for path in glob.glob('/proc/[0-9]*/cmdline'):\n"
            "    try:\n"
            "        found.append(open(path).read())\n"
            "    except OSError:\n"
            "        pass\n"
            f"any('{marker}' in line for line in found)"
        )
        assert seen.ok and seen.output == "False\n", seen
        files = second(f"import glob\nbool(glob.glob('/**/{marker}.txt', recursive=True))")
        assert files.ok and files.output == "False\n", files


def a_kernel_reaches_no_network(provider):
    with _kernel(provider) as run:
        result = run(
            "import socket\n"
            "try:\n"
            "    connection = socket.create_connection(('93.184.215.14', 80), timeout=5)\n"
            "    connection.sendall(b'GET / HTTP/1.1\\r\\nHost: example.com\\r\\n\\r\\n')\n"
            "    answered = bool(connection.recv(64))\n"
            "except OSError:\n"
            "    answered = False\n"
            "answered",
            timeout=30,
        )
        assert result.ok and result.output == "False\n", result


def deleted_data_leaves_the_workspace(provider):
    with _kernel(provider) as run, _kernel(provider, run.project) as other:
        name = f"{uuid.uuid4().hex}.txt"
        provider.put_data(run.project, name, io.BytesIO(b"customer"))
        run("open('note.txt', 'w').write('note')")
        provider.forget(run.project, [name], [run.conversation])
        assert not provider.has_data(run.project, name)
        after = run("import os\nsorted(os.listdir('.'))")
        assert after.ok and "note.txt" not in after.output, after
        provider.put_data(run.project, name, io.BytesIO(b"customer"))
        other("1")
        provider.remove(run.project)
        provider.remove(run.project)
        assert not provider.has_data(run.project, name)


def the_kernel_holds_no_secret_and_tokenizes_offline(provider):
    """Holds only where the provider is deployed with its image: not in a test server."""
    with _kernel(provider) as run:
        result = run(
            "import os, tiktoken\n"
            "assert not any(name in os.environ for name in ('SANDBOX_TOKEN', 'DAYTONA_API_KEY'))\n"
            "len(tiktoken.get_encoding('o200k_base').encode('offline'))",
            timeout=120,
        )
        assert result.ok and result.output == "1\n", result


def a_file_read_back_is_one_the_kernel_wrote(provider):
    with _kernel(provider) as run:
        made = run(
            "import os\n"
            "open('own.txt', 'w').write('own')\n"
            "os.symlink('/etc/hostname', 'host')\n"
            "os.symlink('/proc/1/environ', 'environ')\n"
            "os.symlink('own.txt', 'near')"
        )
        assert made.ok, made
        assert provider.read_file(run.project, run.conversation, "own.txt") == b"own"
        for name in ("host", "environ", "near"):
            try:
                provider.read_file(run.project, run.conversation, name)
            except FileNotFoundError:
                continue
            raise AssertionError(f"The link {name} was followed.")


def output_written_past_the_runner_keeps_the_kernel(provider):
    with _kernel(provider) as run:
        run("kept = 5")
        run("import os, sys\nos.write(1, b'raw')\nos.write(2, b'raw')\nsys.__stdout__.write('raw')")
        assert run("kept").output == "5\n"
        run("import sys\nsys.__stdout__.write('__overmind_reply__:{}')\nsys.__stdout__.flush()")
        assert run("kept").output == "5\n"
        echoed = run("print('__overmind_reply__:')")
        assert echoed.ok and echoed.output == "__overmind_reply__:\n", echoed


CHECKS = (
    variables_persist_and_the_last_expression_is_shown,
    an_exception_returns_its_traceback_and_keeps_the_variables,
    data_is_copied_once_and_read_only,
    conversations_of_a_project_share_data_and_not_variables,
    the_pinned_libraries_work,
    a_cell_past_its_deadline_is_interrupted_and_the_variables_stay,
    long_output_is_bounded_and_says_so,
    a_stopped_kernel_restarts_with_its_files,
    a_kernel_that_dies_is_reported_and_replaced,
    a_project_sees_no_data_or_process_of_another,
    a_kernel_reaches_no_network,
    deleted_data_leaves_the_workspace,
    a_file_read_back_is_one_the_kernel_wrote,
    output_written_past_the_runner_keeps_the_kernel,
)
ISOLATED_CHECKS = (the_kernel_holds_no_secret_and_tokenizes_offline,)
