"""Preferencias da UI em settings.json (escrita atomica, sem Qt)."""

import json
import os
from pathlib import Path

from neuralmeter.config import get_data_dir

DEFAULTS: dict = {"hud_pos": None, "opacity": 1.0, "compact": False, "click_through": False}


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
