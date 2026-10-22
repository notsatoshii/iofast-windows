"""Windows-safe defaults for the test suite.

On Windows the default text encoding is the system codepage (cp949 on Korean
locales, cp1252 on Western ones).  The fixtures are UTF-8 and the engine
writes UTF-8 everywhere, but many tests call ``Path.read_text()`` /
``Path.write_text()`` without an explicit ``encoding=`` argument.  Setting
``PYTHONUTF8=1`` at process startup makes those calls use UTF-8 by default,
matching macOS/Linux behaviour and keeping the tests green cross-platform.
"""

import os
import sys

# Must be set before any I/O happens; pytest imports conftest early enough.
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")