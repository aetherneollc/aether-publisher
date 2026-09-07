"""Tests for health.py's individual probes and HealthState's aggregation.

`probe_browser_launch()` is tested against the real (not mocked) lazy
import path in platforms/browser.py: this dev/test environment deliberately
does not have patchright installed (see README "Development"), so this
exercises the actual "patchright not installed" failure mode rather than a
simulation of it — which is exactly the kind of thing this probe exists to
catch on a real VPS where Chromium/patchright silently broke.
"""

from __future__ import annotations

import pytest

from dusk_publisher.health import HealthState, probe_browser_launch, probe_cookie_volume, probe_disk


def test_probe_disk_reports_free_space(tmp_path):
    ok, free_mb = probe_disk(tmp_path, min_free_mb=1)
    assert ok is True
    assert free_mb > 0


def test_probe_disk_fails_below_threshold(tmp_path):
    ok, free_mb = probe_disk(tmp_path, min_free_mb=10**9)  # absurdly high, always fails
    assert ok is False


def test_probe_cookie_volume_writable(tmp_path):
    ok, err = probe_cookie_volume(tmp_path / "accounts")
    assert ok is True
    assert err is None
    # the marker file must not be left behind
    assert list((tmp_path / "accounts").iterdir()) == []


def test_probe_cookie_volume_not_writable_when_parent_is_a_file(tmp_path):
    # Environment-independent way to force a write failure (works whether or
    # not the test runner happens to be root, unlike chmod-based approaches).
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("x")
    ok, err = probe_cookie_volume(blocker / "accounts")
    assert ok is False
    assert err


@pytest.mark.asyncio
async def test_probe_browser_launch_reports_missing_patchright():
    ok, error = await probe_browser_launch(timeout_s=5)
    assert ok is False
    assert error
    assert "patchright" in error.lower() or "chromium" in error.lower()


def test_health_state_ok_true_before_any_probe_ran():
    state = HealthState()
    assert state.ok is True  # not-yet-probed ("None") is not "proven broken"
    assert state.compute_last_error() is None


def test_health_state_ok_false_when_browser_bad():
    state = HealthState()
    state.browser_ok, state.browser_error = False, "chromium launch failed: boom"
    assert state.ok is False
    assert state.compute_last_error() == "browser_launch_failed: chromium launch failed: boom"


def test_health_state_last_error_priority_browser_before_disk():
    state = HealthState()
    state.browser_ok, state.browser_error = False, "boom"
    state.disk_ok, state.disk_free_mb = False, 3
    err = state.compute_last_error()
    assert err.startswith("browser_launch_failed")


def test_health_state_last_error_names_disk_when_only_disk_bad():
    state = HealthState()
    state.browser_ok = True
    state.disk_ok, state.disk_free_mb = False, 3
    state.cookie_volume_ok = True
    state.control_plane_ok = True
    err = state.compute_last_error()
    assert err == "disk_full: only 3MB free"


def test_health_state_last_error_names_cookie_volume():
    state = HealthState()
    state.browser_ok, state.disk_ok, state.control_plane_ok = True, True, True
    state.cookie_volume_ok, state.cookie_volume_error = False, "Read-only file system"
    err = state.compute_last_error()
    assert err == "cookie_volume_not_writable: Read-only file system"


def test_health_state_last_error_names_control_plane():
    state = HealthState()
    state.browser_ok, state.disk_ok, state.cookie_volume_ok = True, True, True
    state.control_plane_ok, state.control_plane_error = False, "/ops/work: unauthorized"
    err = state.compute_last_error()
    assert err == "control_plane_unreachable: /ops/work: unauthorized"


def test_to_heartbeat_payload_matches_contract_shape_exactly():
    state = HealthState()
    state.browser_ok = True
    state.disk_ok, state.disk_free_mb = True, 20480
    state.cookie_volume_ok = True
    state.control_plane_ok = True
    state.queue_depth = 2
    state.record_success()

    payload = state.to_heartbeat_payload()

    # Exactly these keys, per "字段名请严格按下面这份" — no more, no less.
    assert set(payload.keys()) == {
        "ok",
        "version",
        "uptime_s",
        "browser_ok",
        "disk_free_mb",
        "queue_depth",
        "last_error",
        "last_success_at",
    }
    assert payload["ok"] is True
    assert payload["last_error"] is None
    assert payload["queue_depth"] == 2
    assert payload["disk_free_mb"] == 20480
    assert isinstance(payload["uptime_s"], int)
    assert payload["last_success_at"] is not None


def test_to_debug_dict_is_a_superset_with_checks_breakdown():
    state = HealthState()
    state.browser_ok, state.browser_error = False, "boom"
    debug = state.to_debug_dict()
    assert debug["ok"] is False
    assert debug["checks"]["browser_ok"] is False
    assert debug["checks"]["browser_error"] == "boom"
