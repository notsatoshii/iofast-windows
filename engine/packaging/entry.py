"""PyInstaller entry point for the frozen sidecar (CLI + `iofast rpc`)."""

import sys

from iofast_engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
