from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from neuralmeter.ui.hud import HudWindow, UpdateBridge


def test_flags(qapp):
    hud = HudWindow()
    f = hud.windowFlags()
    assert f & Qt.WindowType.FramelessWindowHint
    assert f & Qt.WindowType.WindowStaysOnTopHint
    assert f & Qt.WindowType.Tool


def test_claude_cli_source_is_displayed_and_disconnect_clears_status(qapp):
    hud = HudWindow()
    hud.set_quota_visibility({key: key == "claude_cli_5h" for key in hud._quota_visibility})
    bridge = UpdateBridge()
    bridge.updated.connect(hud.on_update)
    bridge.updated.emit(
        "cli",
        {"status": "available", "five_hour": {"percent": 62, "reset_at": None}},
    )
    assert "62%" in hud.cli_status.text()
    bridge.updated.emit("cli", {"status": "disconnected"})
    assert "desconectado" in hud.cli_status.text()
    assert not hasattr(hud, "rows")


def test_codex_cli_quota_uses_visible_codex_source(qapp):
    hud = HudWindow()
    hud.set_quota_visibility({key: key == "codex_cli_primary" for key in hud._quota_visibility})
    hud.on_update(
        "codex",
        {
            "status": "available",
            "windows": [{"scope": "primary", "percent": 38, "reset_at": None}],
        },
    )
    assert "Codex CLI:" in hud.codex_status.text()
    assert "primary: 38% usado" in hud.codex_status.text()
    assert "secondary" not in hud.codex_status.text()
    assert not hasattr(hud, "rows")


def test_hidden_quota_selection_keeps_source_connection_state_visible(qapp):
    hud = HudWindow()
    hud.set_quota_visibility({key: False for key in hud._quota_visibility})
    hud.set_cli_status(
        {
            "status": "available",
            "auth_status": "authenticated",
            "five_hour": {"percent": 25, "reset_at": None},
        }
    )
    assert "Claude Code: conectado com dados" in hud.cli_status.text()
    assert "25%" not in hud.cli_status.text()
    assert not hud.cli_status.isHidden()
    assert not hud.codex_status.isHidden()
    assert not hud.antigravity_status.isHidden()
    assert hud.empty_quota_label.text() == "Nenhuma quota selecionada"
    assert not hud.empty_quota_label.isHidden()
    assert hud.empty_quota_action.text() == "Escolher quotas"
    assert not hud.empty_quota_action.isHidden()
    assert not hud.manage_button.isHidden()
    assert hud.height() >= 270
    requests = []
    hud.request_quota_visibility.connect(lambda: requests.append(True))
    hud.empty_quota_action.click()
    assert requests == [True]
    assert all(value is False for value in hud._quota_visibility.values())
    assert hud._payloads["claude"]["status"] == "available"
    hud.set_compact(True)
    assert hud.empty_quota_compact_action.text() == "Mostrar quotas CLI"
    assert not hud.empty_quota_compact_action.isHidden()
    assert hud.height() >= 200


def test_cli_expired_window_percent_is_not_shown_as_current(qapp):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    hud = HudWindow(clock=lambda: now)
    hud.set_quota_visibility({key: key == "claude_cli_5h" for key in hud._quota_visibility})
    hud.set_cli_status(
        {
            "status": "available",
            "five_hour": {"percent": 81, "reset_at": now - timedelta(seconds=1)},
        }
    )
    assert "quota desconhecida (reset vencido)" in hud.cli_status.text()
    assert "81%" not in hud.cli_status.text()


def test_codex_snapshot_ttl_clears_old_percent(qapp):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    hud = HudWindow(clock=lambda: now)
    hud.set_quota_visibility({key: key == "codex_cli_primary" for key in hud._quota_visibility})
    hud.set_codex_status(
        {
            "status": "available",
            "observed_at": now - timedelta(seconds=121),
            "windows": [{"scope": "primary", "percent": 46, "reset_at": None}],
        }
    )
    hud.tick()
    assert "46%" not in hud.codex_status.text()
    assert "desconhecido" in hud.codex_status.text()


def test_antigravity_dynamic_bucket_selection_filters_without_zeroes(qapp):
    hud = HudWindow()
    hud.set_quota_visibility(
        {**{key: False for key in hud._quota_visibility}, "antigravity_flash_weekly": True}
    )
    hud.set_antigravity_status(
        {
            "status": "available",
            "buckets": [
                {
                    "bucket_id": "flash",
                    "model": "Gemini Flash",
                    "window": "weekly",
                    "used_percent": 25,
                    "reset_at": None,
                },
                {
                    "bucket_id": "pro",
                    "model": "Gemini Pro",
                    "window": "weekly",
                    "used_percent": None,
                    "reset_at": None,
                },
            ],
        }
    )
    assert "Gemini Flash" in hud.antigravity_status.text()
    assert "25%" in hud.antigravity_status.text()
    assert "identidade de conta não exposta" in hud.antigravity_status.text()
    assert "Gemini Pro" not in hud.antigravity_status.text()
    assert not hasattr(hud, "rows")


def test_cli_reset_expiry_invalidates_only_affected_bucket_on_tick(qapp):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    hud = HudWindow(clock=lambda: now)
    hud.set_quota_visibility(
        {
            **{key: False for key in hud._quota_visibility},
            "codex_cli_primary": True,
            "codex_cli_secondary": True,
        }
    )
    hud.set_codex_status(
        {
            "status": "available",
            "observed_at": now,
            "windows": [
                {"limit_id": "codex", "scope": "primary", "percent": 51, "reset_at": now},
                {
                    "limit_id": "codex",
                    "scope": "secondary",
                    "percent": 75,
                    "reset_at": now + timedelta(hours=1),
                },
            ],
        }
    )
    hud.tick()
    assert "51%" not in hud.codex_status.text()
    assert "75%" in hud.codex_status.text()


def test_antigravity_reset_expiry_drops_only_expired_bucket_percent(qapp):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    hud = HudWindow(clock=lambda: now)
    hud.set_quota_visibility({"antigravity": True})
    hud.set_antigravity_status(
        {
            "status": "available",
            "observed_at": now,
            "buckets": [
                {"bucket_id": "flash", "window": "5h", "used_percent": 20, "reset_at": now},
                {
                    "bucket_id": "pro",
                    "window": "weekly",
                    "used_percent": 70,
                    "reset_at": now + timedelta(hours=1),
                },
            ],
        }
    )
    hud.tick()
    assert "20%" not in hud.antigravity_status.text()
    assert "70%" in hud.antigravity_status.text()


def test_cached_codex_snapshot_reapplies_new_visibility_without_refreshing_age(qapp):
    now = datetime(2026, 11, 14, 12, tzinfo=UTC)
    hud = HudWindow(clock=lambda: now)
    observed = now - timedelta(seconds=25)
    payload = {
        "status": "available",
        "observed_at": observed,
        "windows": [
            {"limit_id": "codex", "scope": "primary", "percent": 15, "window_minutes": 300},
            {"limit_id": "codex", "scope": "secondary", "percent": 63, "window_minutes": 10080},
        ],
    }
    hud.set_codex_status(payload)
    hud.set_quota_visibility(
        {
            **{key: False for key in hud._quota_visibility},
            "codex_cli_secondary": True,
        }
    )
    assert "15" not in hud.codex_status.text()
    assert "63" in hud.codex_status.text()
    assert "10080m" in hud.codex_compact.text()
    assert hud._codex_observed_at == observed
    hud.set_quota_visibility({key: False for key in hud._quota_visibility})
    assert "63" not in hud.codex_status.text()


def test_cached_codex_weekly_only_filter_survives_settings_reload(qapp, tmp_path):
    from neuralmeter.ui.settings import load_settings, parse_quota_visibility, save_settings

    path = tmp_path / "settings.json"
    saved = {
        **HudWindow()._quota_visibility,
        "codex_cli_primary": False,
        "codex_cli_secondary": True,
    }
    save_settings({"quota_visibility": saved}, path)
    loaded = parse_quota_visibility(load_settings(path)["quota_visibility"])
    hud = HudWindow()
    hud.set_codex_status(
        {
            "status": "available",
            "windows": [
                {"scope": "primary", "percent": 31, "window_minutes": 300},
                {"scope": "secondary", "percent": 77, "window_minutes": 10080},
            ],
        }
    )
    hud.set_quota_visibility(loaded)
    assert "31" not in hud.codex_status.text()
    assert "77" in hud.codex_status.text()


def test_default_expanded_size(qapp):
    hud = HudWindow()
    assert (hud.width(), hud.height()) == (550, 270)
    assert hud.stack.currentWidget() is hud.expanded_page


def test_compact_initial(qapp):
    hud = HudWindow(compact=True)
    assert (hud.width(), hud.height()) == (320, 150)
    assert hud.stack.currentWidget() is hud.compact_page


def test_set_compact_callback_once(qapp):
    calls = []
    hud = HudWindow(on_compact_changed=calls.append)
    hud.set_compact(True)
    hud.set_compact(True)
    assert calls == [True]
    assert (hud.width(), hud.height()) == (320, 150)


def test_double_click_toggles(qapp):
    calls = []
    hud = HudWindow(on_compact_changed=calls.append)
    QTest.mouseDClick(hud, Qt.MouseButton.LeftButton)
    assert hud.compact is True
    QTest.mouseDClick(hud, Qt.MouseButton.LeftButton)
    assert hud.compact is False
    assert calls == [True, False]


def test_hover_expands_then_returns(qapp):
    calls = []
    hud = HudWindow(compact=True, on_compact_changed=calls.append)
    qapp.sendEvent(hud, QEvent(QEvent.Type.Enter))
    assert (hud.width(), hud.height()) == (550, 270)
    qapp.sendEvent(hud, QEvent(QEvent.Type.Leave))
    assert hud._leave_timer.isActive()
    hud._leave_timer.timeout.emit()
    assert (hud.width(), hud.height()) == (320, 150)
    assert hud.compact is True
    assert calls == []


def test_hover_ignored_while_dragging(qapp):
    hud = HudWindow(compact=True)
    hud._drag_offset = QPoint(1, 1)
    qapp.sendEvent(hud, QEvent(QEvent.Type.Enter))
    assert (hud.width(), hud.height()) == (320, 150)


def test_apply_saved_position_none_inside_screen(qapp):
    hud = HudWindow()
    hud.apply_saved_position(None)
    g = QGuiApplication.screens()[0].availableGeometry()
    assert g.contains(hud.x(), hud.y())
    assert hud.x() + hud.width() <= g.x() + g.width()


def test_set_opacity_clamps_and_signals(qapp):
    hud = HudWindow()
    seen = []
    hud.opacity_changed.connect(seen.append)
    hud.set_opacity(50)
    assert abs(hud.windowOpacity() - 0.5) < 0.02
    hud.set_opacity(50)
    assert seen == [50]
    hud.set_opacity(10)
    assert abs(hud.windowOpacity() - 0.2) < 0.02
    assert seen == [50, 20]


def test_initial_opacity_and_click_through(qapp):
    hud = HudWindow(opacity=40, click_through=True)
    assert abs(hud.windowOpacity() - 0.4) < 0.02
    assert hud.windowFlags() & Qt.WindowType.WindowTransparentForInput


def test_set_click_through_flags_pos_signal(qapp):
    hud = HudWindow()
    hud.show()
    hud.move(123, 77)
    pos = hud.pos()
    seen = []
    hud.click_through_changed.connect(seen.append)
    hud.set_click_through(True)
    keep = (
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
    )
    assert hud.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert hud.windowFlags() & keep == keep
    assert hud.pos() == pos
    hud.set_click_through(True)
    hud.set_click_through(False)
    assert not hud.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert hud.pos() == pos
    assert seen == [True, False]
    hud.close()


def test_click_through_cancels_hover(qapp):
    hud = HudWindow(compact=True)
    hud.enterEvent(None)
    assert not hud.effective_compact
    hud.set_click_through(True)
    assert hud.effective_compact


def test_toggle_visible(qapp):
    hud = HudWindow()
    hud.show()
    hud.toggle_visible()
    assert not hud.isVisible()
    hud.toggle_visible()
    assert hud.isVisible()
    hud.close()
