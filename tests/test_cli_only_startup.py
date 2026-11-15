from datetime import UTC, datetime

from PySide6.QtCore import QTimer

from neuralmeter.db import connect_db, insert_usage_event
from neuralmeter.ui.app import run_app
from neuralmeter.ui.settings import load_settings, save_settings


def test_cli_only_startup_preserves_old_sqlite_history_and_does_not_opt_in(
    qapp, tmp_path, monkeypatch
):
    from neuralmeter.ui import app as app_module

    monkeypatch.setattr(app_module, "QApplication", lambda _args: qapp)
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    db = connect_db()
    insert_usage_event(
        db,
        "claude",
        "legacy-history-fixture",
        created_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    db.close()
    save_settings(
        {
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
    )

    QTimer.singleShot(0, qapp.quit)
    assert run_app() == 0

    persisted = connect_db()
    row = persisted.execute(
        "SELECT model_name FROM usage_events WHERE model_name='legacy-history-fixture'"
    ).fetchone()
    assert row["model_name"] == "legacy-history-fixture"
    persisted.close()

    settings = load_settings()
    assert settings.get("claude_cli_enabled", False) is False
    assert settings.get("codex_cli_enabled", False) is False
    assert settings.get("antigravity_enabled", False) is False
    assert settings["cli_only_visibility_migrated"] is True
    assert all(
        settings["quota_visibility"][key] is False
        for key in (
            "claude_cli_5h",
            "claude_cli_weekly",
            "codex_cli_primary",
            "codex_cli_secondary",
            "antigravity",
        )
    )
