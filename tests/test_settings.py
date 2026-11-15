from neuralmeter.ui.settings import (
    DEFAULTS,
    QUOTA_VISIBILITY_DEFAULTS,
    load_settings,
    migrate_cli_quota_visibility,
    parse_quota_visibility,
    save_settings,
)


def test_missing_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "s.json") == DEFAULTS


def test_quota_visibility_whitelist_defaults_and_boolean_validation():
    visible = parse_quota_visibility(
        {
            "claude_cli_5h": False,
            "codex_cli_primary": True,
            "unrecognized_quota": True,
            "secret": True,
        }
    )
    assert visible["claude_cli_5h"] is False
    assert visible["codex_cli_primary"] is True
    assert visible["antigravity"] is True
    assert "secret" not in visible
    assert "unrecognized_quota" not in visible


def test_quota_visibility_keeps_static_defaults_and_caps_dynamic_overrides_separately():
    raw = {f"antigravity_bucket{i}_weekly": i % 2 == 0 for i in range(300)}
    parsed = parse_quota_visibility(raw)
    dynamic = [key for key in parsed if key.startswith("antigravity_")]
    assert len(dynamic) == 256
    assert parsed["claude_cli_5h"] is True
    assert parsed["codex_cli_primary"] is True


def test_legacy_web_preset_preserves_explicit_all_false_cli_visibility_and_consent(tmp_path):
    settings = {
        "quota_visibility": {
            "claude_web_5h": True,
            "chatgpt_web_5h": True,
            "claude_cli_5h": False,
            "claude_cli_weekly": False,
            "codex_cli_primary": False,
            "codex_cli_secondary": False,
            "antigravity": False,
        },
        "claude_cli_enabled": False,
        "codex_cli_enabled": False,
        "antigravity_enabled": False,
    }
    assert migrate_cli_quota_visibility(settings)
    assert all(settings["quota_visibility"][key] is False for key in QUOTA_VISIBILITY_DEFAULTS)
    assert settings["claude_cli_enabled"] is False
    assert settings["codex_cli_enabled"] is False
    assert settings["antigravity_enabled"] is False
    assert not migrate_cli_quota_visibility(settings)
    path = tmp_path / "settings.json"
    save_settings(settings, path)
    reloaded = load_settings(path)
    assert parse_quota_visibility(reloaded["quota_visibility"]) == {
        key: False for key in QUOTA_VISIBILITY_DEFAULTS
    }
    assert reloaded["claude_cli_enabled"] is False
    assert reloaded["codex_cli_enabled"] is False
    assert reloaded["antigravity_enabled"] is False


def test_explicit_cli_visibility_choice_survives_migration():
    settings = {
        "quota_visibility": {
            "claude_web_5h": True,
            "claude_cli_5h": True,
            "claude_cli_weekly": False,
            "codex_cli_primary": False,
            "codex_cli_secondary": False,
            "antigravity": False,
        },
    }
    migrate_cli_quota_visibility(settings)
    assert settings["quota_visibility"]["claude_cli_5h"] is True
    assert settings["quota_visibility"]["codex_cli_primary"] is False
    assert settings["quota_visibility"]["antigravity"] is False


def test_missing_or_invalid_cli_visibility_fields_use_visible_defaults(tmp_path):
    assert parse_quota_visibility({}) == QUOTA_VISIBILITY_DEFAULTS
    parsed = parse_quota_visibility(
        {"claude_cli_5h": False, "claude_cli_weekly": "false", "codex_cli_primary": None}
    )
    assert parsed["claude_cli_5h"] is False
    assert parsed["claude_cli_weekly"] is True
    assert parsed["codex_cli_primary"] is True
    assert parsed["codex_cli_secondary"] is True
    assert parsed["antigravity"] is True
    path = tmp_path / "partial-settings.json"
    save_settings({"quota_visibility": {"claude_cli_5h": False}}, path)
    reloaded = load_settings(path)
    resolved = parse_quota_visibility(reloaded.get("quota_visibility"))
    assert resolved["claude_cli_5h"] is False
    assert resolved["claude_cli_weekly"] is True
    assert resolved["codex_cli_primary"] is True
    assert resolved["antigravity"] is True


def test_dynamic_quota_visibility_roundtrip_and_disappeared_bucket(tmp_path):
    p = tmp_path / "settings.json"
    key = "antigravity_model_flash_weekly"
    save_settings({"quota_visibility": {key: True}}, p)
    loaded = load_settings(p)
    assert parse_quota_visibility(loaded["quota_visibility"])[key] is True


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


def test_settings_file_read_is_bounded_to_64kib(tmp_path):
    p = tmp_path / "large-settings.json"
    p.write_bytes(b" " * (64 * 1024 + 1))
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
