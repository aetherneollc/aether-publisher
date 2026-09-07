"""CLI smoke test: `dusk-publisher queue run` end-to-end via the actual
Typer command, same real-adapter/no-cookie setup as
test_local_runner_e2e.py. This is the literal command a user runs for the
"local queue mode self-test" described in the project brief.
"""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from dusk_publisher.cli import app

runner = CliRunner()


def test_queue_run_cli_reaches_login_prompt_and_exits_zero(tmp_path, data_dir, fake_media, monkeypatch):
    queue_file = tmp_path / "local_queue.yaml"
    queue_file.write_text(
        yaml.safe_dump(
            {
                "languages": {"zh": {"name": "简体中文"}},
                "publications": [
                    {
                        "pub_id": "long1",
                        "story_id": "s",
                        "episode_code": "e1",
                        "lang": "zh",
                        "platform": "youtube",
                        "kind": "long",
                        "asset_key": "demo.mp4",
                        "title": "demo",
                        "status": "approved",
                    }
                ],
            },
            allow_unicode=True,
        )
    )
    accounts_file = tmp_path / "accounts.yaml"
    accounts_file.write_text(
        yaml.safe_dump({"accounts": [{"platform": "youtube", "name": "yt_main", "lang": None}]})
    )
    monkeypatch.setenv("ACCOUNTS_FILE", str(accounts_file))

    result = runner.invoke(
        app,
        [
            "queue",
            "run",
            "--file",
            str(queue_file),
            "--media-root",
            str(fake_media["root"]),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "published: 0" in result.output
    assert "LOGIN long1 needs `dusk-publisher youtube login --account yt_main`" in result.output
