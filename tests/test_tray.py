import time
from datetime import UTC, datetime

from PySide6.QtWidgets import QLabel

from neuralmeter.claude_cli import CliSnapshot, QuotaPoller, QuotaWindow
from neuralmeter.codex_cli import CodexSnapshot, CodexWindow
from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.quota_visibility_dialog import QuotaVisibilityDialog
from neuralmeter.ui.tray import build_tray, make_tray_icon


def test_icon_not_null(qapp):
    assert not make_tray_icon().isNull()


def test_actions_drive_hud(qapp):
    hud = HudWindow()
    quits = []
    tray = build_tray(hud, lambda: quits.append(1))
    assert tray.toolTip() == "NeuralMeter"
    tray.act_click_through.trigger()
    assert hud.click_through
    tray.act_compact.trigger()
    assert hud.compact
    tray.opacity_actions[40].trigger()
    assert hud.opacity == 40
    assert tray.opacity_actions[40].isChecked()
    assert not tray.opacity_actions[100].isChecked()
    tray.act_quit.trigger()
    assert quits == [1]


def test_hud_changes_sync_menu(qapp):
    hud = HudWindow(opacity=60)
    tray = build_tray(hud, lambda: None)
    assert tray.opacity_actions[60].isChecked()
    hud.set_click_through(True)
    assert tray.act_click_through.isChecked()
    hud.set_compact(True)
    assert tray.act_compact.isChecked()
    hud.set_opacity(80)
    assert tray.opacity_actions[80].isChecked()
    assert not tray.opacity_actions[60].isChecked()
    hud.set_compact(False)
    assert not tray.act_compact.isChecked()


def test_toggle_action_and_activation(qapp):
    from PySide6.QtWidgets import QSystemTrayIcon

    hud = HudWindow()
    hud.show()
    tray = build_tray(hud, lambda: None)
    tray.act_toggle.trigger()
    assert not hud.isVisible()
    tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert hud.isVisible()
    hud.close()


def test_menu_order(qapp):
    tray = build_tray(HudWindow(), lambda: None)
    items = [("-" if a.isSeparator() else a.text()) for a in tray.menu.actions()]
    assert items == [
        "Mostrar/Ocultar HUD",
        "Modo compacto",
        "Opacidade",
        "Click-through",
        "-",
        "Avisar renovação 5h",
        "Configurar limites…",
        "Iniciar com o Windows",
        "-",
        "Conectar Claude CLI",
        "Conectar Codex CLI",
        "Conectar Antigravity (experimental)",
        "-",
        "Quotas exibidas…",
        "-",
        "Modo privado (hash IDs)",
        "Apagar histórico local…",
        "-",
        "Sair",
    ]


def test_cli_opt_in_toggle_labels_connect_and_disconnect(qapp):
    values = []
    tray = build_tray(HudWindow(), lambda: None, on_cli_changed=values.append)
    assert tray.act_cli.text() == "Conectar Claude CLI"
    tray.act_cli.setChecked(True)
    assert tray.act_cli.text() == "Desconectar Claude CLI"
    tray.act_cli.setChecked(False)
    assert tray.act_cli.text() == "Conectar Claude CLI"
    assert values == [True, False]


def test_codex_opt_in_is_independent(qapp):
    values = []
    tray = build_tray(HudWindow(), lambda: None, on_codex_changed=values.append)
    tray.act_codex.setChecked(True)
    assert tray.act_codex.text() == "Desconectar Codex CLI"
    assert values == [True]


def test_antigravity_opt_in_is_independent(qapp):
    values = []
    tray = build_tray(HudWindow(), lambda: None, on_antigravity_changed=values.append)
    tray.act_antigravity.setChecked(True)
    assert "Desconectar Antigravity" in tray.act_antigravity.text()
    assert values == [True]


def test_quota_visibility_dialog_separates_selection_from_cli_opt_in(qapp):
    dialog = QuotaVisibilityDialog(
        {"codex_cli_primary": True, "claude_cli_5h": False},
        {"antigravity_gemini_flash_weekly": "Antigravity · Gemini Flash · weekly"},
    )
    assert dialog.checkboxes["codex_cli_primary"].isChecked()
    assert not dialog.checkboxes["claude_cli_5h"].isChecked()
    dialog.checkboxes["claude_cli_5h"].setChecked(True)
    assert dialog.selection()["claude_cli_5h"]
    assert dialog.selection()["codex_cli_primary"]
    assert "antigravity_gemini_flash_weekly" in dialog.selection()


def test_quota_visibility_dialog_scrolls_and_preserves_all_valid_dynamic_rows(qapp):
    available = {
        f"antigravity_bucket{i}_weekly": "Antigravity quota " + ("long label " * 12) + str(i)
        for i in range(90)
    }
    saved_key = "antigravity_bucket89_weekly"
    dialog = QuotaVisibilityDialog(
        {saved_key: True},
        available,
    )
    assert len(dialog.checkboxes) == 95
    assert dialog.checkboxes[saved_key].isChecked()
    assert dialog.selection()["claude_cli_5h"]
    assert all(
        not box.isChecked()
        for key, box in dialog.checkboxes.items()
        if key.startswith("antigravity_") and key != saved_key
    )
    assert dialog.height() <= dialog.maximumHeight()
    dialog.checkboxes[saved_key].setChecked(False)
    assert dialog.selection()[saved_key] is False


def test_quota_visibility_dialog_marks_omitted_unpersistible_buckets(qapp):
    available = {f"codex_cli_limit{i}_primary": f"Quota {i}" for i in range(300)}
    dialog = QuotaVisibilityDialog({}, available)
    dynamic = [key for key in dialog.checkboxes if key.startswith("codex_cli_limit")]
    assert len(dynamic) == 256
    assert any("Cobertura parcial" in label.text() for label in dialog.findChildren(QLabel))


def test_quota_visibility_dynamic_cap_fairly_covers_both_sources(qapp):
    available = {
        **{f"codex_cli_limit{i}_primary": f"Codex {i}" for i in range(140)},
        **{f"antigravity_bucket{i}_weekly": f"Antigravity {i}" for i in range(140)},
    }
    dialog = QuotaVisibilityDialog({}, available)
    dynamic_keys = [
        key
        for key in dialog.checkboxes
        if key.startswith("codex_cli_limit") or key.startswith("antigravity_bucket")
    ]
    assert len(dynamic_keys) == 256
    assert sum(key.startswith("codex_cli_") for key in dynamic_keys) == 128
    assert sum(key.startswith("antigravity_") for key in dynamic_keys) == 128
    assert any("Cobertura parcial" in label.text() for label in dialog.findChildren(QLabel))


def test_quota_bucket_override_survives_snapshot_disappear_and_reappear(qapp):
    key = "antigravity_flash_weekly"
    disappeared = QuotaVisibilityDialog({key: True}, {})
    assert disappeared.checkboxes[key].isChecked()
    saved = disappeared.selection()
    reappeared = QuotaVisibilityDialog({key: True, **saved}, {key: "Gemini Flash · weekly"})
    assert reappeared.checkboxes[key].isChecked()


def test_tray_opt_in_worker_snapshot_reaches_separate_hud_then_disconnects(qapp):
    hud = HudWindow()
    bridge = UpdateBridge()
    bridge.updated.connect(hud.on_update)
    pollers = []

    def toggle(enabled):
        if pollers:
            pollers.pop().stop()
        if enabled:
            snapshot = CliSnapshot(
                "available",
                QuotaWindow(52, None),
                QuotaWindow(None, None),
                datetime.now(UTC),
            )
            poller = QuotaPoller(
                lambda result: bridge.updated.emit(
                    "cli",
                    {
                        "status": result.status,
                        "observed_at": result.observed_at,
                        "five_hour": {"percent": result.five_hour.percent, "reset_at": None},
                    },
                ),
                fetch=lambda: snapshot,
            )
            pollers.append(poller)
            poller.start()
        else:
            bridge.updated.emit("cli", {"status": "disconnected"})

    tray = build_tray(hud, lambda: None, cli_enabled=False, on_cli_changed=toggle)
    hud.set_quota_visibility({key: key == "claude_cli_5h" for key in hud._quota_visibility})
    tray.act_cli.setChecked(True)
    deadline = time.monotonic() + 2
    while "52%" not in hud.cli_status.text() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert "52%" in hud.cli_status.text()
    assert not hasattr(hud, "rows")
    tray.act_cli.setChecked(False)
    assert "desconectado" in hud.cli_status.text()


def test_codex_tray_opt_in_worker_snapshot_stays_codex_work(qapp):
    hud = HudWindow()
    hud.set_quota_visibility({key: key == "codex_cli_primary" for key in hud._quota_visibility})
    bridge = UpdateBridge()
    bridge.updated.connect(hud.on_update)
    pollers = []

    def toggle(enabled):
        if pollers:
            pollers.pop().stop()
        if enabled:
            snapshot = CodexSnapshot(
                "available", (CodexWindow("primary", 47, None),), datetime.now(UTC)
            )
            poller = QuotaPoller(
                lambda result: bridge.updated.emit(
                    "codex",
                    {
                        "status": result.status,
                        "windows": [
                            {"scope": row.scope, "percent": row.percent, "reset_at": row.reset_at}
                            for row in result.windows
                        ],
                    },
                ),
                fetch=lambda: snapshot,
            )
            pollers.append(poller)
            poller.start()
        else:
            bridge.updated.emit("codex", {"status": "not_connected"})

    tray = build_tray(hud, lambda: None, on_codex_changed=toggle)
    tray.act_codex.setChecked(True)
    deadline = time.monotonic() + 2
    while "47%" not in hud.codex_status.text() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert "Codex CLI:" in hud.codex_status.text()
    assert "primary: 47% usado" in hud.codex_status.text()
    assert "ChatGPT" not in hud.codex_status.text()
    tray.act_codex.setChecked(False)
    assert "não conectado" in hud.codex_status.text()


def test_autostart_toggle_and_revert(qapp, monkeypatch):
    from neuralmeter.ui import tray as tray_mod

    calls = []
    monkeypatch.setattr(tray_mod, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(tray_mod, "set_autostart", lambda v: calls.append(v) or True)
    tray = build_tray(HudWindow(), lambda: None)
    assert not tray.act_autostart.isChecked()
    tray.act_autostart.setEnabled(True)
    tray.act_autostart.trigger()
    assert tray.act_autostart.isChecked()
    assert calls == [True]
    monkeypatch.setattr(tray_mod, "set_autostart", lambda v: calls.append(v) or False)
    tray.act_autostart.trigger()  # tenta desligar, falha -> reverte
    assert tray.act_autostart.isChecked()
    assert calls == [True, False]


def test_configure_limits_refreshes_hud(qapp, tmp_data_dir, monkeypatch):
    from neuralmeter import db
    from neuralmeter.ui import tray as tray_mod

    hud = HudWindow()
    refreshed, saved = [], []
    monkeypatch.setattr(hud, "refresh", lambda: refreshed.append(1))
    monkeypatch.setattr(tray_mod, "open_limits_dialog", lambda conn: True)
    tray = build_tray(
        hud, lambda: None, conn=db.connect_db(), on_limits_saved=lambda: saved.append(1)
    )
    tray.act_limits.trigger()
    assert refreshed == [1] and saved == [1]
    monkeypatch.setattr(tray_mod, "open_limits_dialog", lambda conn: False)
    tray.act_limits.trigger()
    assert refreshed == [1]


def test_notify_renewal_toggle(qapp, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    shown, saved = [], []
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a, **k: shown.append(a))
    tray = build_tray(HudWindow(), lambda: None, on_notify_changed=saved.append)
    assert tray.act_notify.isChecked()
    tray.notify_renewal("claude")
    assert len(shown) == 1 and "Claude" in shown[0][1]
    tray.act_notify.trigger()
    assert saved == [False]
    tray.notify_renewal("claude")
    assert len(shown) == 1


def test_hud_renewed_signal_reaches_tray(qapp, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    shown = []
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a, **k: shown.append(a))
    hud = HudWindow()
    tray = build_tray(hud, lambda: None)
    hud.renewed.emit("chatgpt")
    assert tray is not None
    assert len(shown) == 1 and "ChatGPT" in shown[0][1]


def test_private_mode_toggle_calls_back(qapp):
    got = []
    tray = build_tray(HudWindow(), lambda: None, on_privacy_changed=got.append)
    assert tray.act_private.isChecked()
    tray.act_private.trigger()
    assert got == [False]


def test_clear_history(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from neuralmeter.db import connect_db, insert_usage_event

    conn = connect_db(tmp_path / "t.db")
    insert_usage_event(conn, "claude", "m")
    tray = build_tray(HudWindow(), lambda: None, conn=conn)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert tray._clear_history() == 0
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert tray._clear_history() == 1
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 0
    conn.close()
