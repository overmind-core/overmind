"""What every session provider must do. The test suite runs these against the native
provider; ``manage.py check_sandbox_sessions`` runs them against the configured one, which
is the only way to check a hosted provider."""

import io
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from overbae.services.sandbox_sessions import Execution, SessionGoneError, SessionProvider


@contextmanager
def _session(provider: SessionProvider) -> Iterator[Callable[..., Execution]]:
    session = provider.start()

    def run(code: str, *, execution_id: str | None = None, timeout: int = 60) -> Execution:
        return provider.execute(
            session, code, execution_id=execution_id or uuid.uuid4().hex, timeout=timeout
        )

    run.session = session
    try:
        yield run
    finally:
        provider.stop(session)


def namespace_persists_and_the_last_expression_is_shown(provider):
    with _session(provider) as run:
        assert run("rows = [1, 2, 3]").output == ""
        result = run("print('total')\nsum(rows)")
        assert result.ok and result.output == "total\n6\n", result


def an_exception_returns_its_traceback_and_keeps_the_namespace(provider):
    with _session(provider) as run:
        run("kept = 41")
        failed = run("def broken():\n    return 1 / 0\nbroken()")
        assert not failed.ok, failed
        assert "ZeroDivisionError" in failed.error and "return 1 / 0" in failed.error, failed
        assert run("kept + 1").output == "42\n"


def one_execution_id_runs_once(provider):
    with _session(provider) as run:
        run("calls = 0")
        execution_id = uuid.uuid4().hex
        first = run("calls += 1\ncalls", execution_id=execution_id)
        again = run("calls += 1\ncalls", execution_id=execution_id)
        assert first.output == again.output == "1\n", (first, again)
        changed = run("calls += 5", execution_id=execution_id)
        assert not changed.ok and "different code" in changed.error, changed
        assert run("calls").output == "1\n"


def files_move_in_and_out(provider):
    content = b"\x00overmind\xff" * 1000
    with _session(provider) as run:
        provider.put_file(run.session, "source.bin", io.BytesIO(content))
        result = run(
            "data = open('../inputs/source.bin', 'rb').read()\n"
            "open('copy.bin', 'wb').write(data[::-1])\nlen(data)"
        )
        assert result.output == "10000\n", result
        assert provider.get_file(run.session, "copy.bin") == content[::-1]
        kept = run(
            "try:\n"
            "    open('../inputs/source.bin', 'ab')\n"
            "except OSError:\n"
            "    kept = True\n"
            "else:\n"
            "    kept = False\n"
            "kept"
        )
        assert kept.output == "True\n", kept
        try:
            provider.get_file(run.session, "absent.bin")
        except FileNotFoundError:
            return
        raise AssertionError("A missing file was returned.")


# Every pinned library, used the way a session uses it: a library that cannot reach a file it
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
    with _session(provider) as run:
        result = run(LIBRARY_USE, timeout=300)
        assert result.ok, result


def a_cell_past_its_deadline_is_interrupted_and_the_session_survives(provider):
    with _session(provider) as run:
        run("marker = 'alive'")
        slow = run("while True:\n    pass", timeout=1)
        assert not slow.ok and "interrupted" in slow.error, slow
        assert run("marker").output == "'alive'\n"


def long_output_is_bounded_and_says_so(provider):
    with _session(provider) as run:
        result = run("print('x' * 200_000)")
        assert result.truncated and len(result.output) < 70_000, result.truncated
        assert "characters omitted" in result.output


def _gone(provider, session):
    try:
        provider.execute(session, "1", execution_id=uuid.uuid4().hex, timeout=10)
    except SessionGoneError:
        return True
    return False


def a_stopped_session_is_gone_and_stop_is_idempotent(provider):
    session = provider.start()
    provider.stop(session)
    provider.stop(session)
    assert _gone(provider, session)


def a_kernel_that_dies_reports_the_session_as_gone(provider):
    with _session(provider) as run:
        try:
            run("import os\nos._exit(1)")
        except SessionGoneError:
            assert _gone(provider, run.session)
            return
        raise AssertionError("A dead kernel answered.")


def a_session_sees_no_process_of_another_session(provider):
    marker = uuid.uuid4().hex
    with _session(provider) as first, _session(provider) as second:
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


def a_session_opens_no_network_socket(provider):
    with _session(provider) as run:
        refused = run(
            "import socket\n"
            "try:\n"
            "    socket.socket()\n"
            "except PermissionError:\n"
            "    refused = True\n"
            "else:\n"
            "    refused = False\n"
            "refused"
        )
        assert refused.ok and refused.output == "True\n", refused


def the_kernel_holds_no_secret_and_tokenizes_offline(provider):
    """Holds only where the provider is deployed with its image: not in a test server."""
    with _session(provider) as run:
        result = run(
            "import os, tiktoken\n"
            "assert not any(name in os.environ for name in ('SANDBOX_TOKEN', 'DAYTONA_API_KEY'))\n"
            "len(tiktoken.get_encoding('o200k_base').encode('offline'))",
            timeout=120,
        )
        assert result.ok and result.output == "1\n", result


CHECKS = (
    namespace_persists_and_the_last_expression_is_shown,
    an_exception_returns_its_traceback_and_keeps_the_namespace,
    one_execution_id_runs_once,
    files_move_in_and_out,
    the_pinned_libraries_work,
    a_cell_past_its_deadline_is_interrupted_and_the_session_survives,
    long_output_is_bounded_and_says_so,
    a_stopped_session_is_gone_and_stop_is_idempotent,
    a_kernel_that_dies_reports_the_session_as_gone,
    a_session_sees_no_process_of_another_session,
    a_session_opens_no_network_socket,
)
ISOLATED_CHECKS = (the_kernel_holds_no_secret_and_tokenizes_offline,)
