#!/usr/bin/env python3
"""Generate a tiny, real (playable) fake .mp4 + matching .jpg cover for
local-queue self-testing, using ffmpeg if available.

Usage:
    python scripts/make_fake_video.py <output.mp4> [--seconds 2] [--cover output.jpg]

Falls back to writing a minimal non-playable placeholder file if ffmpeg is
not on PATH — good enough for tests that only check file *existence* and
never actually open the file in a browser (which is the case for every
committed test here: they intentionally stop at the "needs login" gate,
before any browser ever launches).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def make_with_ffmpeg(ffmpeg: str, out_path: Path, seconds: int, cover_path: Path | None) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=navy:s=320x180:d={seconds}",
        "-c:v",
        "libx264",
        "-t",
        str(seconds),
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode(errors="replace"))
        return False
    if cover_path:
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-i", str(out_path), "-frames:v", "1", str(cover_path)],
            capture_output=True,
        )
    return True


def make_placeholder(out_path: Path, cover_path: Path | None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)  # not really playable, just non-empty
    if cover_path:
        cover_path.parent.mkdir(parents=True, exist_ok=True)
        cover_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)  # JPEG-ish header, placeholder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=2)
    parser.add_argument("--cover", type=Path, default=None)
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        ok = make_with_ffmpeg(ffmpeg, args.output, args.seconds, args.cover)
        if not ok:
            print("ffmpeg failed, falling back to placeholder file", file=sys.stderr)
            make_placeholder(args.output, args.cover)
    else:
        make_placeholder(args.output, args.cover)

    print(f"wrote {args.output}" + (f" + {args.cover}" if args.cover else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
