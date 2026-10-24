"""stderr_pump: a parent that never reads stderr cannot stall the engine.

Regression for the Windows cancel hang: verify_frozen reads the sidecar's
stderr only after shutdown, the ~4 KB pipe filled, and the scrape thread
blocked in a logging write, so a cancelled crawl never saved its checkpoint.
"""

import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from iofast_engine.stderr_pump import StderrPump

SRC = os.path.join(os.path.dirname(__file__), "..", "src")

CHILD = textwrap.dedent(
    """
    import logging, sys
    from iofast_engine.stderr_pump import install
    pump = install() if sys.argv[1] == "pump" else None
    log = logging.getLogger("noisy")
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    for i in range(20000):  # ~1.5 MB: far past any OS pipe buffer
        log.info("fetched page %05d of a long docs crawl", i)
    sys.stdout.write("finished\\n")
    sys.stdout.flush()
    """
)


def _run_child(mode, read_stderr):
    env = {**os.environ, "PYTHONPATH": os.path.abspath(SRC), "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, mode],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        if read_stderr:
            out, err = proc.communicate(timeout=60)
            return out, err
        line = proc.stdout.readline()  # stderr is deliberately never read
        return line, None
    finally:
        proc.kill()
        proc.wait()


def test_undrained_stderr_does_not_block_writes():
    line, _ = _run_child("pump", read_stderr=False)
    assert line.strip() == b"finished"


def test_drained_stderr_still_reaches_parent():
    out, err = _run_child("pump", read_stderr=True)
    assert out.strip() == b"finished"
    assert b"page 00000" in err and b"page 19999" in err


def test_buffer_drops_oldest_past_cap_and_says_so():
    read_fd, write_fd = os.pipe()
    out_r, out_w = os.pipe()
    pump = StderrPump(read_fd, out_w, cap=1000)
    # Pump only, no forwarder yet: the parent is "not reading".
    reader = threading.Thread(target=pump._pump, daemon=True)
    reader.start()
    line = b"x" * 99 + b"\n"
    for _ in range(50):
        os.write(write_fd, line)
    os.close(write_fd)
    reader.join(timeout=10)
    assert pump.size == 1000 and pump.dropped == 4000
    threading.Thread(target=pump._forward, daemon=True).start()
    assert pump.flush(timeout=5)
    os.close(out_w)
    data = b""
    while chunk := os.read(out_r, 65536):
        data += chunk
    assert data == b"[iofast] stderr not read; dropped 4000 bytes\n" + line * 10


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 std handle only")
def test_win32_std_handle_follows_the_pump():
    line, _ = _run_child_handle_probe()
    assert line.strip() == b"same"


def _run_child_handle_probe():
    probe = textwrap.dedent(
        """
        import ctypes, msvcrt, sys
        from iofast_engine.stderr_pump import install
        install()
        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = ctypes.c_void_p
        same = k.GetStdHandle(-12) == msvcrt.get_osfhandle(2)
        sys.stdout.write("same\\n" if same else "different\\n")
        sys.stdout.flush()
        """
    )
    env = {**os.environ, "PYTHONPATH": os.path.abspath(SRC)}
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, env=env, timeout=60
    )
    return proc.stdout, proc.stderr
