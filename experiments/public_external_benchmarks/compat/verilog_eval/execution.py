"""Safe evaluator helpers expected by RTLFixer's local VerilogExecutor.

The upstream release/1.0.0 execution.py is syntactically invalid.  RTLFixer
does not call its disabled check_correctness function; it imports only these
context-manager primitives and implements compilation locally.
"""
from __future__ import annotations

import contextlib
import io
import os
import signal
import tempfile
from pathlib import Path


class TimeoutException(Exception):
    pass


class WriteOnlyStringIO(io.StringIO):
    def read(self, *args, **kwargs):
        raise IOError("stream is write-only")

    readline = read
    readlines = read

    def readable(self, *args, **kwargs):
        return False


class redirect_stdin(contextlib._RedirectStream):
    _stream = "stdin"


@contextlib.contextmanager
def time_limit(seconds: float):
    def handler(_signum, _frame):
        raise TimeoutException("Timed out")

    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextlib.contextmanager
def swallow_io():
    stream = WriteOnlyStringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream), redirect_stdin(stream):
        yield


@contextlib.contextmanager
def chdir(root):
    previous = Path.cwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(previous)


@contextlib.contextmanager
def create_tempdir():
    with tempfile.TemporaryDirectory() as dirname, chdir(dirname):
        yield dirname


def reliability_guard(maximum_memory_bytes=None):
    """Compatibility no-op; process/time isolation is supplied by the adapter."""
    os.environ["OMP_NUM_THREADS"] = "1"


def clean_up_simulation():
    for name in ("test.vvp", "wave.vcd"):
        path = Path(name)
        if path.is_file():
            path.unlink()
