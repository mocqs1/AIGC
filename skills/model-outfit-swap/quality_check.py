"""CLI wrapper for the importable model outfit swap artifact checker."""

from pathlib import Path
import sys

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from skills.model_outfit_swap.quality_check import check_artifact  # noqa: E402


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Check a model outfit swap image")
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps(check_artifact(args.path), indent=2))
