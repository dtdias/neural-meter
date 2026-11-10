from neuralmeter.ui.settings import DEFAULTS, load_settings, save_settings


def test_missing_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "s.json") == DEFAULTS


def test_roundtrip_and_no_tmp(tmp_path):
    p = tmp_path / "s.json"
    data = load_settings(p)
    data["hud_pos"] = (10, 20)
    save_settings(data, p)
    assert load_settings(p)["hud_pos"] == [10, 20]
    assert [f.name for f in tmp_path.iterdir()] == ["s.json"]


def test_corrupt_gives_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{nope", encoding="utf-8")
    assert load_settings(p) == DEFAULTS
    p.write_text("[1,2]", encoding="utf-8")
    assert load_settings(p) == DEFAULTS


def test_unknown_key_preserved(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"foo": 1}', encoding="utf-8")
    d = load_settings(p)
    assert d["foo"] == 1 and d["opacity"] == 1.0
    save_settings(d, p)
    assert load_settings(p)["foo"] == 1


def test_default_path_uses_data_dir(tmp_data_dir):
    save_settings({"hud_pos": [1, 2]})
    assert (tmp_data_dir / "settings.json").exists()
