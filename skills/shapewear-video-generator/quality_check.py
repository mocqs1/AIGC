"""CLI wrapper for the importable Shapewear artifact checker."""

from pathlib import Path
import sys

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from skills.shapewear_video_generator.quality_check import check_artifact, main  # noqa: E402


if __name__ == "__main__":
    main()
