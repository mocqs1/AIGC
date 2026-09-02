"""Command-line uploader for single files and directories."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uploader.exceptions import R2Error
from uploader.r2_client import ALLOWED_EXTENSIONS, R2Client, generate_object_key, normalize_object_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upload AIGC images and videos to Cloudflare R2")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("file", nargs="?", help="Local image or video file")
    source.add_argument("--dir", dest="directory", help="Directory of supported media files")
    parser.add_argument("--key", help="Explicit object key (single-file mode only)")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing explicit object key")
    return parser


def _print_single(path: Path, key: str, url: str, *, overwrite: bool) -> None:
    print("========================================")
    print("R2 Upload")
    print("========================================")
    print(f"\nFile:\n{path}\n")
    print(f"Object:\n{key}\n")
    if overwrite:
        print("Overwrite mode:\nENABLED\n")
    print("Status:\nSUCCESS\n")
    print(f"Public URL:\n{url}\n")
    print("========================================")


def main(argv: list[str] | None = None, *, client: R2Client | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.directory and (args.key or args.overwrite):
        parser.error("--key and --overwrite can only be used with a single file")
    if args.overwrite and not args.key:
        parser.error("--overwrite requires --key")
    try:
        active_client = client or R2Client()
        if args.file:
            path = Path(args.file)
            key = normalize_object_key(args.key) if args.key else generate_object_key(path)
            url = active_client.upload_file(path, key, overwrite=args.overwrite)
            _print_single(path, key, url, overwrite=args.overwrite)
            return 0

        directory = Path(args.directory)
        if not directory.exists() or not directory.is_dir():
            raise ValueError(f"Directory does not exist: {directory}")
        files = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS)
        if not files:
            print("No supported media files found.")
            return 0
        failures = 0
        print("FILE\tSTATUS\tOBJECT KEY\tPUBLIC URL")
        for path in files:
            key = generate_object_key(path)
            try:
                url = active_client.upload_file(path, key)
                print(f"{path.name}\tSUCCESS\t{key}\t{url}")
            except (R2Error, OSError, ValueError) as error:
                failures += 1
                print(f"{path.name}\tFAILED\t{key}\t{error}")
        return 1 if failures else 0
    except (R2Error, OSError, ValueError) as error:
        if "Object key already exists" in str(error) and args.file:
            print(
                "R2 upload failed: Object key already exists. Choose another --key or rerun with --overwrite.",
                file=sys.stderr,
            )
        else:
            print(f"R2 upload failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
