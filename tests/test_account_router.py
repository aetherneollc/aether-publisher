import pytest

from dusk_publisher.accounts.router import AccountEntry, AccountRouter
from dusk_publisher.errors import AccountNotFoundError


def test_resolve_lang_specific_match():
    router = AccountRouter(
        [
            AccountEntry(platform="youtube", name="yt_zh", lang="zh"),
            AccountEntry(platform="youtube", name="yt_generic", lang=None),
        ]
    )
    assert router.resolve("youtube", "zh") == "yt_zh"


def test_resolve_falls_back_to_generic():
    router = AccountRouter(
        [
            AccountEntry(platform="youtube", name="yt_zh", lang="zh"),
            AccountEntry(platform="youtube", name="yt_generic", lang=None),
        ]
    )
    assert router.resolve("youtube", "en") == "yt_generic"


def test_resolve_no_lang_requested_uses_generic():
    router = AccountRouter(
        [
            AccountEntry(platform="youtube", name="yt_zh", lang="zh"),
            AccountEntry(platform="youtube", name="yt_generic", lang=None),
        ]
    )
    assert router.resolve("youtube", None) == "yt_generic"


def test_resolve_raises_when_nothing_matches():
    router = AccountRouter([AccountEntry(platform="youtube", name="yt_zh", lang="zh")])
    with pytest.raises(AccountNotFoundError):
        router.resolve("youtube", "en")


def test_resolve_raises_for_unknown_platform():
    router = AccountRouter([AccountEntry(platform="youtube", name="yt_zh", lang="zh")])
    with pytest.raises(AccountNotFoundError):
        router.resolve("tiktok", "zh")


def test_load_accounts_file_missing_returns_empty(tmp_path):
    from dusk_publisher.accounts.router import load_accounts_file

    assert load_accounts_file(tmp_path / "nope.yaml") == []


def test_load_accounts_file_roundtrip(tmp_path):
    from dusk_publisher.accounts.router import load_accounts_file

    f = tmp_path / "accounts.yaml"
    f.write_text(
        "accounts:\n"
        "  - platform: tiktok\n"
        "    name: tt_zh\n"
        "    lang: zh\n"
        "  - platform: tiktok\n"
        "    name: tt_generic\n"
    )
    entries = load_accounts_file(f)
    assert len(entries) == 2
    assert entries[0].name == "tt_zh"
    assert entries[1].lang is None
