from neuralmeter.ui.hud import HudWindow
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
        "Modo privado (hash IDs)",
        "Apagar histórico local…",
        "-",
        "Sair",
    ]


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
