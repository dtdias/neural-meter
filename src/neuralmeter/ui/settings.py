"""Preferencias da UI em settings.json (escrita atomica, sem Qt)."""

import json
import os
import secrets
import string
from datetime import timedelta
from pathlib import Path

from neuralmeter.config import get_data_dir
from neuralmeter.engine import DEFAULT_WEIGHT_PARAMS, ModelLimit, WeightParams
from neuralmeter.privacy import PrivacySettings, clamp_retention_days

DEFAULTS: dict = {"hud_pos": None, "opacity": 100, "compact": False, "click_through": False}


OPACITY_PRESETS = (20, 40, 60, 80, 100)


def clamp_opacity(v) -> int:
    """Opacidade em % inteiro no intervalo [20, 100]; invalido -> 100."""
    if isinstance(v, bool) or v is None:
        return 100
    try:
        n = round(float(v))
    except (TypeError, ValueError, OverflowError):
        return 100
    return max(20, min(100, n))


def parse_model_limits(raw) -> dict[str, ModelLimit]:
    """settings['chatgpt_model_limits'] -> {modelo_minusculo: ModelLimit}; invalidos ignorados."""
    out: dict[str, ModelLimit] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(val, dict):
            continue
        hours = val.get("window_hours")
        units = val.get("max_units")
        if isinstance(hours, bool) or hours not in (3, 5):
            continue
        if isinstance(units, bool) or not isinstance(units, int | float):
            continue
        if not (0 < units < float("inf")):
            continue
        out[key.strip().lower()] = ModelLimit(timedelta(hours=int(hours)), float(units))
    return out


def _num(v, default, *, lo: float, hi: float, lo_open: bool = False):
    if isinstance(v, bool) or not isinstance(v, int | float):
        return default
    if v != v or v > hi or v < lo or (lo_open and v == lo):
        return default
    return v


def parse_weight_params(raw) -> WeightParams:
    """settings['claude_weight'] -> WeightParams; campo invalido -> default daquele campo.

    PREMISSA peso Claude (progress.md Decisoes, 2026-11-10)."""
    d = DEFAULT_WEIGHT_PARAMS
    if not isinstance(raw, dict):
        return d
    return WeightParams(
        base_chars=_num(raw.get("base_chars"), d.base_chars, lo=0, hi=10_000_000),
        step_chars=_num(raw.get("step_chars"), d.step_chars, lo=0, hi=10_000_000, lo_open=True),
        max_weight=_num(raw.get("max_weight"), d.max_weight, lo=1, hi=1000),
    )


def parse_privacy_settings(settings) -> PrivacySettings:
    """settings['privacy'] + settings['privacy_salt'] -> PrivacySettings; invalido -> default."""
    raw = settings.get("privacy") if isinstance(settings, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    flag = raw.get("hash_conversation_ids")
    salt = settings.get("privacy_salt") if isinstance(settings, dict) else None
    return PrivacySettings(
        hash_conversation_ids=flag if isinstance(flag, bool) else True,
        retention_days=clamp_retention_days(raw.get("retention_days")),
        salt=salt if isinstance(salt, str) else "",
    )


def ensure_privacy_salt(settings: dict) -> bool:
    """Garante settings['privacy_salt'] (hex de 32 chars). True se mudou (chamador salva)."""
    salt = settings.get("privacy_salt")
    if isinstance(salt, str) and len(salt) == 32 and all(c in string.hexdigits for c in salt):
        return False
    settings["privacy_salt"] = secrets.token_hex(16)
    return True


def settings_path() -> Path:
    return get_data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    path = path or settings_path()
    data = dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if isinstance(raw, dict):
        data.update(raw)
    return data


def save_settings(data: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    out = dict(data)
    if out.get("hud_pos") is not None:
        out["hud_pos"] = [int(out["hud_pos"][0]), int(out["hud_pos"][1])]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
