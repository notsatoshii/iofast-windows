"""The RPC loop must not hold a blocking read on a Windows stdin pipe:
that deadlocked numpy's DLL load in the grab thread (0.2.6 hang)."""
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows pipe behaviour")

CHILD = r"""
import os, sys, threading, time
from iofast_engine.rpc import _lines
def load():
    import numpy
    try:
        import ctranslate2
    except ImportError:
        pass
    os.write(1, b"loaded\n"); os._exit(0)
def watchdog():
    time.sleep(60); os.write(1, b"HUNG\n"); os._exit(1)
threading.Thread(target=watchdog, daemon=True).start()
threading.Thread(target=lambda: (time.sleep(0.5), load()), daemon=True).start()
for line in _lines(sys.stdin):
    pass
time.sleep(70)
"""


def test_dll_load_while_waiting_on_stdin():
    pytest.importorskip("numpy")
    p = subprocess.Popen([sys.executable, "-c", CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        out = p.stdout.readline()
    finally:
        p.kill()
        p.stdin.close()
    assert out.strip() == b"loaded"


def test_lines_split_across_reads():
    child = "import sys\nfrom iofast_engine.rpc import _lines\nfor l in _lines(sys.stdin): print(repr(l))"
    p = subprocess.run([sys.executable, "-c", child], input=b'{"a":1}\n{"b":\n2}\nlast', capture_output=True, timeout=30)
    assert p.stdout.decode().split() == ["'{\"a\":1}'", "'{\"b\":'", "'2}'", "'last'"]
