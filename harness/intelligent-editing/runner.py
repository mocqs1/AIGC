"""Delegate the filesystem entrypoint to the importable Harness package."""

import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harness.intelligent_editing.runner import main


if __name__ == "__main__":
    raise SystemExit(main())
