from dusk_publisher.models import Kind, Publication, PubStatus


def _base(**overrides):
    data = dict(
        pub_id="p1",
        story_id="s1",
        episode_code="ep01",
        lang="zh",
        platform="youtube",
        kind="long",
        asset_key="a.mp4",
        title="t",
    )
    data.update(overrides)
    return Publication(**data)


def test_tags_coerced_from_csv_string():
    pub = _base(tags="a, b ,c")
    assert pub.tags == ["a", "b", "c"]


def test_tags_default_empty():
    pub = _base()
    assert pub.tags == []


def test_is_ready_to_run_only_when_approved():
    pub = _base(status="approved")
    assert pub.is_ready_to_run()
    pub2 = _base(status="published")
    assert not pub2.is_ready_to_run()


def test_is_gated_on_parent_only_for_short_with_parent():
    long_pub = _base(kind="long")
    assert not long_pub.is_gated_on_parent()

    short_no_parent = _base(kind="short", pub_id="p2", platform="tiktok")
    assert not short_no_parent.is_gated_on_parent()

    short_with_parent = _base(kind="short", pub_id="p3", platform="tiktok", parent_pub_id="p1")
    assert short_with_parent.is_gated_on_parent()


def test_status_enum_roundtrip():
    pub = _base(status="failed", error="boom")
    assert pub.status == PubStatus.failed
    assert pub.error == "boom"
    assert pub.kind == Kind.long
