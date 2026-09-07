from dusk_publisher.models import Publication
from dusk_publisher.pipeline.dropback import find_parent, inject_into_description, parent_is_ready


def _pub(**overrides):
    data = dict(
        pub_id="p1",
        story_id="s1",
        episode_code="ep01",
        lang="zh",
        platform="youtube",
        kind="long",
        asset_key="a.mp4",
    )
    data.update(overrides)
    return Publication(**data)


def test_find_parent_found():
    parent = _pub(pub_id="parent1")
    other = _pub(pub_id="other")
    assert find_parent([parent, other], "parent1") is parent


def test_find_parent_missing():
    assert find_parent([_pub(pub_id="x")], "does-not-exist") is None


def test_parent_is_ready_requires_published_status_and_url():
    not_published = _pub(status="approved")
    assert not parent_is_ready(not_published)

    published_no_url = _pub(status="published", url=None)
    assert not parent_is_ready(published_no_url)

    ready = _pub(status="published", url="https://youtu.be/abc")
    assert parent_is_ready(ready)

    assert not parent_is_ready(None)


def test_inject_into_description_appends_once():
    desc = "看正片"
    injected = inject_into_description(desc, "https://youtu.be/abc")
    assert "https://youtu.be/abc" in injected
    assert injected.startswith("看正片")

    # idempotent: running again does not duplicate the line
    injected_again = inject_into_description(injected, "https://youtu.be/abc")
    assert injected_again == injected
    assert injected_again.count("https://youtu.be/abc") == 1


def test_inject_into_description_empty_description():
    injected = inject_into_description("", "https://youtu.be/abc")
    assert injected.strip() == "完整版/full video: https://youtu.be/abc"
