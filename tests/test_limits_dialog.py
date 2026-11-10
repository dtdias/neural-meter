from neuralmeter import db
from neuralmeter.ui import limits_dialog
from neuralmeter.ui.limits_dialog import LimitsDialog


def test_dialog_fills_and_saves(qapp, tmp_data_dir):
    conn = db.connect_db()
    db.set_provider_limits(conn, "chatgpt", weekly_max_units=777)
    dlg = LimitsDialog(conn)
    assert dlg.spins[("claude", "window_5h_max_units")].value() == 45.0
    assert dlg.spins[("chatgpt", "weekly_max_units")].value() == 777.0
    dlg.spins[("claude", "window_5h_max_units")].setValue(60.5)
    dlg.spins[("chatgpt", "window_5h_max_units")].setValue(30)
    dlg.accept()
    assert db.get_provider_limits(conn, "claude") == {
        "window_5h_max_units": 60.5,
        "weekly_max_units": 500.0,
    }
    assert db.get_provider_limits(conn, "chatgpt")["window_5h_max_units"] == 30.0
    assert "chatgpt_model_limits" in dlg.note.text()


def test_reject_does_not_save(qapp, tmp_data_dir):
    conn = db.connect_db()
    dlg = LimitsDialog(conn)
    dlg.spins[("claude", "window_5h_max_units")].setValue(99)
    dlg.reject()
    assert db.get_provider_limits(conn, "claude")["window_5h_max_units"] == 45.0


def test_open_limits_dialog_result(qapp, tmp_data_dir, monkeypatch):
    conn = db.connect_db()
    monkeypatch.setattr(limits_dialog.QDialog, "exec", lambda self: 1)
    assert limits_dialog.open_limits_dialog(conn) is True
    monkeypatch.setattr(limits_dialog.QDialog, "exec", lambda self: 0)
    assert limits_dialog.open_limits_dialog(conn) is False
