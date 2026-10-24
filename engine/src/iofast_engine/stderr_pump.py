"""Keep writes to fd 2 from ever blocking the engine.

The RPC parent reads stderr whenever it likes: the Tauri shell drains it line
by line, a test harness may read it only after shutdown. A pipe the parent is
not reading fills (about 4 KB on Windows, 64 KB on macOS), and the next write
to it blocks. A scrape thread logging one line then hangs mid-crawl, so a
cancel is acknowledged but the checkpoint is never written.

`install()` points fd 2 at a private pipe. A pump thread drains that pipe into
a bounded in-memory buffer (oldest bytes dropped past the cap) and a forwarder
thread writes the buffer to the real stderr. Only the forwarder can block on a
slow parent; Python logging, C extensions and child processes that inherit
stderr never do.
"""

import atexit
import collections
import os
import sys
import threading
import time

CAP_BYTES = 1 << 20  # buffered stderr kept while the parent is not reading
READ_CHUNK = 64 * 1024


class StderrPump:
    def __init__(self, read_fd, out_fd, cap=CAP_BYTES):
        self.read_fd = read_fd
        self.out_fd = out_fd
        self.cap = cap
        self.chunks = collections.deque()
        self.size = 0
        self.dropped = 0
        self.received = 0  # total bytes the pump has taken off the pipe
        self.writing = False
        self.cond = threading.Condition()

    def start(self):
        for target, name in ((self._pump, "stderr-pump"), (self._forward, "stderr-forward")):
            threading.Thread(target=target, name=name, daemon=True).start()
        return self

    def _pump(self):
        while True:
            try:
                data = os.read(self.read_fd, READ_CHUNK)
            except OSError:
                return
            if not data:
                return
            with self.cond:
                self.chunks.append(data)
                self.size += len(data)
                self.received += len(data)
                excess = self.size - self.cap
                while excess > 0:
                    head = self.chunks[0]
                    cut = min(excess, len(head))
                    if cut == len(head):
                        self.chunks.popleft()
                    else:
                        self.chunks[0] = head[cut:]
                    self.size -= cut
                    self.dropped += cut
                    excess -= cut
                self.cond.notify_all()

    def _forward(self):
        while True:
            with self.cond:
                while not self.chunks:
                    self.cond.wait()
                data = b"".join(self.chunks)
                self.chunks.clear()
                self.size = 0
                if self.dropped:
                    note = f"[iofast] stderr not read; dropped {self.dropped} bytes\n"
                    data = note.encode() + data
                    self.dropped = 0
                self.writing = True
            try:
                while data:
                    data = data[os.write(self.out_fd, data):]
            except OSError:
                pass  # parent closed stderr: nothing left to deliver to
            with self.cond:
                self.writing = False
                self.cond.notify_all()

    def flush(self, timeout, quiet=0.05):
        """Best effort: wait up to `timeout` seconds for stderr to reach the parent.

        Done means nothing buffered, nothing being written, and no new bytes
        for `quiet` seconds. (Peeking the pipe is no option on Windows: it
        queues behind the pump's pending read on the same handle.)
        """
        sys.stderr.flush()
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            with self.cond:
                idle = not self.chunks and not self.writing
                seen = self.received
            if idle and seen == last:
                return True
            last = seen if idle else None
            time.sleep(quiet)
        return False


def install(cap=CAP_BYTES):
    """Route fd 2 through a StderrPump; return the running pump."""
    sys.stderr.flush()
    read_fd, write_fd = os.pipe()
    real_fd = os.dup(2)
    os.dup2(write_fd, 2)
    os.close(write_fd)
    if sys.platform == "win32":
        # Children launched with inherited stderr (browser drivers) take the
        # Win32 std handle, which os.dup2 does not change.
        import ctypes
        import msvcrt

        std_error_handle = -12
        ctypes.windll.kernel32.SetStdHandle(std_error_handle, msvcrt.get_osfhandle(2))
    pump = StderrPump(read_fd, real_fd, cap).start()
    atexit.register(pump.flush, 2)  # normal interpreter exit (CLI paths)
    return pump
