"""Account routing: (platform, lang) -> account name.

Policy (per spec): an account can be bound to a language, or left unbound
(generic/fallback). Publishing looks up by (platform, lang) first; if no
language-specific account exists, it falls back to a generic account for
that platform. This makes "one account per language" and "one account for
everything" both pure configuration choices in accounts.yaml, with no code
branch needed either way.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from dusk_publisher.errors import AccountNotFoundError


class AccountEntry(BaseModel):
    platform: str
    name: str
    lang: str | None = None
    notes: str | None = None


class AccountRouter:
    def __init__(self, entries: list[AccountEntry]):
        self._entries = entries

    @classmethod
    def from_file(cls, path: Path) -> "AccountRouter":
        return cls(load_accounts_file(path))

    def resolve(self, platform: str, lang: str | None) -> str:
        """Return the account *name* to use, or raise AccountNotFoundError."""
        candidates = [e for e in self._entries if e.platform == platform]
        if not candidates:
            raise AccountNotFoundError(platform, lang)

        if lang:
            for e in candidates:
                if e.lang == lang:
                    return e.name

        for e in candidates:
            if e.lang is None:
                return e.name

        raise AccountNotFoundError(platform, lang)

    def all_for_platform(self, platform: str) -> list[AccountEntry]:
        return [e for e in self._entries if e.platform == platform]

    def all(self) -> list[AccountEntry]:
        return list(self._entries)


def load_accounts_file(path: Path) -> list[AccountEntry]:
    if not Path(path).exists():
        return []
    raw = yaml.safe_load(Path(path).read_text()) or {}
    items = raw.get("accounts", []) if isinstance(raw, dict) else raw
    return [AccountEntry(**item) for item in items]
