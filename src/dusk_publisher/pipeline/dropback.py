"""Dropback: the core growth-loop logic ("用短视频给长视频引流").

Flow, per language, driven by `parent_pub_id` on the short's publication
record:
  1. The long-form (YouTube) publication for a language publishes first;
     its resulting watch URL is reported back.
  2. Only once that long-form row is `published` does the short's task get
     released (in pull-mode, the control plane enforces this server-side
     before it ever appears from `/ops/work`; in local-queue mode, this
     module's `find_parent` + the runner enforce it ourselves, since there
     is no server to do it for us).
  3. When publishing the short, the parent's URL gets woven into the
     description/caption/tweet text (this module) and, per-adapter,
     best-effort posted+pinned as a top comment/reply after the short goes
     live (see each platform adapter's `_pin_dropback_comment` /
     `_highlight_dropback_reply`).

Language is never hardcoded here or anywhere else in the pipeline — it's
read off each `Publication.lang` and the `languages` registry from
`/ops/work` (or the local queue file's own `languages:` block), so adding a
4th/5th language is a data change, not a code change.
"""

from __future__ import annotations

from dusk_publisher.models import Publication, PubStatus


def find_parent(all_pubs: list[Publication], parent_pub_id: str) -> Publication | None:
    for p in all_pubs:
        if p.pub_id == parent_pub_id:
            return p
    return None


def parent_is_ready(parent: Publication | None) -> bool:
    return parent is not None and parent.status == PubStatus.published and bool(parent.url)


def dropback_line(parent_url: str) -> str:
    return f"完整版/full video: {parent_url}"


def inject_into_description(description: str, parent_url: str) -> str:
    """Append the parent long-form URL to a short's description, once.

    Idempotent: if the URL is already present (e.g. re-run after a partial
    failure), it won't be appended twice.
    """
    if parent_url in description:
        return description
    line = dropback_line(parent_url)
    sep = "\n\n" if description.strip() else ""
    return f"{description}{sep}{line}"
