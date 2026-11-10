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
    assert d["foo"] == 1 and d["opacity"] == 100
    save_settings(d, p)
    assert load_settings(p)["foo"] == 1


def test_default_path_uses_data_dir(tmp_data_dir):
    save_settings({"hud_pos": [1, 2]})
    assert (tmp_data_dir / "settings.json").exists()


def test_clamp_opacity():
    from neuralmeter.ui.settings import clamp_opacity

    assert clamp_opacity(5) == 20
    assert clamp_opacity(150) == 100
    assert clamp_opacity(None) == 100
    assert clamp_opacity("x") == 100
    assert clamp_opacity(55) == 55


def test_parse_model_limits():
    from datetime import timedelta

    from neuralmeter.engine import ModelLimit
    from neuralmeter.ui.settings import parse_model_limits

    raw = {
        "GPT-5": {"window_hours": 5, "max_units": 100},
        "o3": {"window_hours": 3, "max_units": 12.5},
        "bad_hours": {"window_hours": 4, "max_units": 10},
        "bad_units": {"window_hours": 3, "max_units": 0},
        "bad_type": {"window_hours": 3, "max_units": "x"},
        "bool": {"window_hours": 3, "max_units": True},
        "notdict": 5,
        7: {"window_hours": 3, "max_units": 1},
    }
    assert parse_model_limits(raw) == {
        "gpt-5": ModelLimit(timedelta(hours=5), 100.0),
        "o3": ModelLimit(timedelta(hours=3), 12.5),
    }
    assert parse_model_limits(None) == {}
    assert parse_model_limits([1, 2]) == {}
    assert parse_model_limits("x") == {}


def test_parse_weight_params_defaults():
    from neuralmeter.engine import DEFAULT_WEIGHT_PARAMS
    from neuralmeter.ui.settings import parse_weight_params

    for raw in (None, "x", {}, {"step_chars": 0}, {"max_weight": True}):
        assert parse_weight_params(raw) == DEFAULT_WEIGHT_PARAMS


def test_parse_weight_params_valid():
    from neuralmeter.engine import WeightParams
    from neuralmeter.ui.settings import parse_weight_params

    raw = {"base_chars": 0, "step_chars": 1000, "max_weight": 10}
    assert parse_weight_params(raw) == WeightParams(0, 1000, 10)
    assert parse_weight_params({"max_weight": 0.5, "step_chars": 500}).step_chars == 500
    assert parse_weight_params({"max_weight": 5000}).max_weight == 50.0
