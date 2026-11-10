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
