import json

from dusk_publisher.accounts.cookies import cookie_path, has_valid_cookie_file


def test_cookie_path_convention(tmp_path):
    p = cookie_path(tmp_path, "youtube", "zh_main")
    assert p == tmp_path / "accounts" / "youtube" / "zh_main.json"


def test_has_valid_cookie_file_missing(tmp_path):
    assert not has_valid_cookie_file(tmp_path / "nope.json")


def test_has_valid_cookie_file_empty(tmp_path):
    f = tmp_path / "empty.json"
    f.write_text("")
    assert not has_valid_cookie_file(f)


def test_has_valid_cookie_file_garbage(tmp_path):
    f = tmp_path / "garbage.json"
    f.write_text("not json{{{")
    assert not has_valid_cookie_file(f)


def test_has_valid_cookie_file_wrong_shape(tmp_path):
    f = tmp_path / "wrong.json"
    f.write_text(json.dumps({"foo": "bar"}))
    assert not has_valid_cookie_file(f)


def test_has_valid_cookie_file_valid_storage_state(tmp_path):
    f = tmp_path / "valid.json"
    f.write_text(json.dumps({"cookies": [{"name": "sid", "value": "x"}], "origins": []}))
    assert has_valid_cookie_file(f)
