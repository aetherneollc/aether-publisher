from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKE_FAKE_VIDEO = REPO_ROOT / "scripts" / "make_fake_video.py"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("DATA_DIR", str(d))
    return d


def make_fake_video(video_path: Path, cover_path: Path | None = None) -> None:
    video_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(MAKE_FAKE_VIDEO), str(video_path), "--seconds", "1"]
    if cover_path:
        cmd += ["--cover", str(cover_path)]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture
def fake_media(tmp_path):
    """Create a real (ffmpeg-generated) tiny mp4 + jpg cover for self-tests."""
    media_root = tmp_path / "media"
    video = media_root / "demo.mp4"
    cover = media_root / "demo.jpg"
    make_fake_video(video, cover)
    return {"root": media_root, "video": video, "cover": cover}
