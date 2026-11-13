import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.view_model import (
    LEVEL_COLORS,
    ModelQuotaView,
    ProviderView,
    level_for_percent,
)


def test_flags(qapp):
    hud = HudWindow()
    f = hud.windowFlags()
    assert f & Qt.WindowType.FramelessWindowHint
    assert f & Qt.WindowType.WindowStaysOnTopHint
    assert f & Qt.WindowType.Tool


def _view(provider="claude", pct=75.0, reset=None, model="sonnet"):
    return ProviderView(
        provider=provider,
        label=provider.title(),
        pct_5h=pct,
        level_5h=level_for_percent(pct),
        reset_5h=reset,
        pct_week=10.0,
        level_week="green",
        reset_week=None,
        last_model=model,
        rate_limited=False,
    )


def test_renders_percent_and_color(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    reset = now + timedelta(minutes=30)
    views = {"claude": _view(pct=75, reset=reset), "chatgpt": _view("chatgpt", pct=95)}
    hud = HudWindow(view_loader=lambda n: views, clock=lambda: now)
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    row = hud.rows["claude"]
    assert row.bar_5h.value() == 75
    assert LEVEL_COLORS["yellow"] in row.bar_5h.styleSheet()
    assert LEVEL_COLORS["red"] in hud.rows["chatgpt"].bar_5h.styleSheet()
    assert LEVEL_COLORS["green"] in row.bar_week.styleSheet()
    assert "estimado" in row.text_5h.text()
    assert "reset estimado" in row.text_5h.text()
    assert row.model_label.text() == "sonnet"


def test_conn_label_updates(qapp):
    hud = HudWindow()
    assert hud.status_label.text() == "offline"
    hud.on_update("conn", {"clients": 1})
    assert hud.status_label.text() == "aguardando aba"
    hud.on_update("status", {"tabs": {"claude": True, "chatgpt": False}})
    assert hud.status_label.text() == "online"
    hud.on_update("conn", {"clients": 0})
    assert hud.status_label.text() == "offline"


def test_tick_updates_countdown_without_reload(qapp):
    clock = {"now": datetime(2026, 11, 10, 12, 0, tzinfo=UTC)}
    calls = []
    reset = clock["now"] + timedelta(minutes=30)

    def loader(n):
        calls.append(n)
        return {"claude": _view(reset=reset)}

    hud = HudWindow(view_loader=loader, clock=lambda: clock["now"])
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    before = hud.rows["claude"].text_5h.text()
    clock["now"] += timedelta(seconds=10)
    hud.tick()
    assert hud.rows["claude"].text_5h.text() != before
    assert "29m 50s" in hud.rows["claude"].text_5h.text()
    assert len(calls) == 1


def test_usage_and_limit_refresh(qapp):
    calls = []
    hud = HudWindow(view_loader=lambda n: calls.append(n) or {})
    hud.on_update("usage", {})
    hud.on_update("limit", {})
    assert len(calls) == 3


def test_bridge_cross_thread(qapp):
    hud = HudWindow()
    bridge = UpdateBridge()
    bridge.updated.connect(hud.on_update)
    t = threading.Thread(target=bridge.emit_update, args=("conn", {"clients": 2}))
    t.start()
    t.join()
    deadline = time.time() + 1
    while hud.status_label.text() != "aguardando aba" and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert hud.status_label.text() == "aguardando aba"


def test_default_expanded_size(qapp):
    hud = HudWindow()
    assert (hud.width(), hud.height()) == (380, 210)
    assert hud.stack.currentWidget() is hud.expanded_page


def test_compact_initial(qapp):
    hud = HudWindow(compact=True)
    assert (hud.width(), hud.height()) == (300, 116)
    assert hud.stack.currentWidget() is hud.compact_page


def test_set_compact_callback_once(qapp):
    calls = []
    hud = HudWindow(on_compact_changed=calls.append)
    hud.set_compact(True)
    hud.set_compact(True)
    assert calls == [True]
    assert (hud.width(), hud.height()) == (300, 116)


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
    assert (hud.width(), hud.height()) == (380, 210)
    qapp.sendEvent(hud, QEvent(QEvent.Type.Leave))
    assert hud._leave_timer.isActive()
    hud._leave_timer.timeout.emit()
    assert (hud.width(), hud.height()) == (300, 116)
    assert hud.compact is True
    assert calls == []


def test_hover_ignored_while_dragging(qapp):
    hud = HudWindow(compact=True)
    hud._drag_offset = QPoint(1, 1)
    qapp.sendEvent(hud, QEvent(QEvent.Type.Enter))
    assert (hud.width(), hud.height()) == (300, 116)


def test_compact_row_labels(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    limited = replace(_view("chatgpt", pct=95), rate_limited=True)
    views = {"claude": _view(pct=75), "chatgpt": limited}
    hud = HudWindow(view_loader=lambda n: views, clock=lambda: now)
    assert "Estimado" in hud.compact_rows["claude"].text.text()
    assert "75% usado" in hud.compact_rows["claude"].text.text()
    assert hud.compact_rows["claude"].bar.value() == 75
    assert hud.compact_rows["chatgpt"].text.text().startswith("Bloqueado")


def test_stale_status_visible_in_both_hud_modes(qapp):
    from dataclasses import replace

    view = replace(
        _view(pct=42),
        source_5h="estimado · desatualizado",
        reset_source_5h="estimado",
        reset_5h=datetime.now(UTC) + timedelta(minutes=5),
    )
    hud = HudWindow(view_loader=lambda _now: {"claude": view})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    assert "desatualizado" in hud.rows["claude"].text_5h.text()
    assert "desatualizado" in hud.compact_rows["claude"].text.text()
    assert hud.compact_rows["claude"].bar.value() == 42


def test_multiple_claude_tabs_force_estimated_fallback_in_both_modes(qapp):
    view = replace(
        _view(pct=75),
        source_5h="oficial",
        estimated_pct_5h=40,
        estimated_pct_week=12,
        estimated_blocked_5h=False,
        estimated_blocked_week=False,
        estimated_rate_limited=False,
    )
    hud = HudWindow(view_loader=lambda _now: {"claude": view})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 2}})
    assert hud.rows["claude"].bar_5h.value() == 40
    assert "múltiplas abas" in hud.rows["claude"].text_5h.text()
    assert "múltiplas abas" in hud.compact_rows["claude"].text.text()
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    assert hud.rows["claude"].bar_5h.value() == 75


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


def test_renewed_signal_on_tick(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    clock = [now]
    views = {"claude": _view(pct=40, reset=now + timedelta(minutes=5))}
    hud = HudWindow(view_loader=lambda n: views, clock=lambda: clock[0])
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    got = []
    hud.renewed.connect(got.append)
    hud.tick()
    assert got == []
    clock[0] = now + timedelta(minutes=6)
    hud.tick()
    hud.tick()
    assert got == ["claude"]


@pytest.mark.parametrize("compact", [False, True])
def test_official_ttl_and_reset_expire_on_tick_without_reload(qapp, compact):
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)
    boundary_5h = now + timedelta(seconds=10)
    boundary_week = now + timedelta(seconds=20)
    base = replace(
        _view(pct=81),
        source_5h="oficial",
        source_week="oficial",
        reset_source_5h="oficial",
        reset_source_week="oficial",
        official_until_5h=boundary_5h,
        official_until_week=boundary_week,
        reset_5h=boundary_5h,
        reset_week=boundary_week,
        estimated_pct_5h=24,
        estimated_pct_week=13,
        estimated_reset_5h=None,
        estimated_reset_week=None,
    )
    clock = [now]
    reloads = []
    hud = HudWindow(
        view_loader=lambda value: reloads.append(value) or {"claude": base},
        clock=lambda: clock[0],
        compact=compact,
    )
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    initial_reloads = len(reloads)
    clock[0] = boundary_5h - timedelta(microseconds=1)
    hud.tick()
    row = hud.compact_rows["claude"].text.text() if compact else hud.rows["claude"].text_5h.text()
    assert "oficial" in row
    clock[0] = boundary_5h
    hud.tick()
    row = hud.compact_rows["claude"].text.text() if compact else hud.rows["claude"].text_5h.text()
    assert "desatualizado" in row and "24%" in row
    assert "reset estimado: —" in row
    assert len(reloads) == initial_reloads
    clock[0] = boundary_5h + timedelta(microseconds=1)
    hud.tick()
    row = hud.compact_rows["claude"].text.text() if compact else hud.rows["claude"].text_5h.text()
    assert "desatualizado" in row and "24%" in row
    clock[0] = boundary_week
    hud.tick()
    row = hud.compact_rows["claude"].text.text() if compact else hud.rows["claude"].text_week.text()
    if not compact:
        assert "13% · estimado · desatualizado" in row
        assert "reset estimado: —" in row
    hud.close()


def test_identity_disconnect_and_source_transition_cancel_pending_toast(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    clock = [now]
    official = replace(
        _view(pct=40, reset=now + timedelta(seconds=5)),
        source_5h="oficial",
        reset_source_5h="oficial",
        official_until_5h=now + timedelta(seconds=3),
        estimated_pct_5h=22,
        estimated_reset_5h=None,
        identity_state="confirmed",
    )
    hud = HudWindow(view_loader=lambda _n: {"claude": official}, clock=lambda: clock[0])
    got = []
    hud.renewed.connect(got.append)
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 2}})
    clock[0] = now + timedelta(seconds=6)
    hud.tick()
    assert got == []
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    hud.on_update("conn", {"clients": 0})
    hud.tick()
    assert got == []
    hud.close()


def test_account_identity_change_cannot_replay_old_reset_toast(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    clock = [now]
    current = [
        replace(
            _view(pct=40, reset=now + timedelta(seconds=5)),
            source_5h="oficial",
            reset_source_5h="oficial",
            quota_identity="a" * 64,
            identity_state="confirmed",
            quota_fresh_5h=True,
            quota_observed_at_5h=now,
        )
    ]
    hud = HudWindow(view_loader=lambda _n: {"claude": current[0]}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    got = []
    hud.renewed.connect(got.append)
    current[0] = replace(
        current[0],
        reset_5h=now - timedelta(seconds=1),
        quota_identity="b" * 64,
    )
    clock[0] = now + timedelta(seconds=1)
    hud.refresh()
    hud.tick()
    assert got == []
    hud.close()


def test_confirmed_official_reset_fires_once_at_exact_boundary(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    boundary = now + timedelta(seconds=5)
    confirmed = replace(
        _view(pct=42, reset=boundary),
        source_5h="oficial",
        reset_source_5h="oficial",
        official_until_5h=boundary,
        quota_identity="a" * 64,
        identity_state="confirmed",
        quota_state="fresh",
        quota_fresh_5h=True,
        official_reset_5h=boundary,
        quota_observed_at_5h=now,
        estimated_pct_5h=19,
        estimated_reset_5h=None,
    )
    clock = [now]
    hud = HudWindow(view_loader=lambda _n: {"claude": confirmed}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    clock[0] = now + timedelta(microseconds=1)
    confirmed = replace(confirmed, quota_observed_at_5h=clock[0])
    hud.refresh()
    got = []
    hud.renewed.connect(got.append)
    clock[0] = boundary - timedelta(microseconds=1)
    hud.tick()
    assert "claude" in hud._renewal_armed, repr(
        (hud._renewal_source, hud._renewal_invalidated_at, hud._renewal._state)
    )
    assert got == []
    clock[0] = boundary
    hud.tick()
    assert got == ["claude"]
    hud.tick()
    assert got == ["claude"]
    assert "estimado" in hud.rows["claude"].text_5h.text()
    assert "reset estimado: —" in hud.rows["claude"].text_5h.text()
    hud.close()


@pytest.mark.parametrize(
    ("percent", "blocked", "boundary_offset", "expected"),
    [
        (0, False, timedelta(0), []),
        (35, False, -timedelta(microseconds=1), ["claude"]),
        (35, False, timedelta(0), ["claude"]),
        (35, False, timedelta(microseconds=1), ["claude"]),
        (0, True, timedelta(0), ["claude"]),
    ],
)
def test_official_reset_emits_only_when_armed_generation_was_eligible(
    qapp, percent, blocked, boundary_offset, expected
):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    reset = now + timedelta(seconds=5)
    clock = [now]
    view = replace(
        _view(pct=percent, reset=reset),
        level_5h=level_for_percent(percent, blocked),
        rate_limited=blocked,
        source_5h="oficial",
        reset_source_5h="oficial",
        official_until_5h=reset,
        quota_identity="a" * 64,
        identity_state="confirmed",
        quota_state="fresh",
        quota_fresh_5h=True,
        official_reset_5h=reset,
        quota_observed_at_5h=now,
    )
    hud = HudWindow(view_loader=lambda _n: {"claude": view}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    clock[0] = now + timedelta(microseconds=1)
    hud.refresh()  # Fresh confirmation after initial offline state.
    fired = []
    hud.renewed.connect(fired.append)
    clock[0] = reset + boundary_offset
    hud.tick()
    assert fired == ([] if boundary_offset < timedelta(0) else expected)
    clock[0] = reset + timedelta(microseconds=1)
    hud.tick()
    hud.tick()
    assert fired == expected
    hud.close()


@pytest.mark.parametrize("before,after,expected", [(35, 0, []), (0, 35, ["claude"])])
def test_official_generation_eligibility_tracks_latest_observation(before, after, expected, qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    reset = now + timedelta(seconds=5)
    clock = [now]

    def snapshot(percent):
        return replace(
            _view(pct=percent, reset=reset),
            source_5h="oficial",
            reset_source_5h="oficial",
            official_until_5h=reset,
            quota_identity="a" * 64,
            identity_state="confirmed",
            quota_state="fresh",
            quota_fresh_5h=True,
            official_reset_5h=reset,
            quota_observed_at_5h=now + timedelta(microseconds=1),
        )

    current = [snapshot(before)]
    hud = HudWindow(view_loader=lambda _n: {"claude": current[0]}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    fired = []
    hud.renewed.connect(fired.append)
    clock[0] = now + timedelta(seconds=1)
    current[0] = snapshot(after)
    # Same generation; latest observed percent supplies eligibility.
    hud.refresh()
    clock[0] = reset
    hud.tick()
    hud.tick()
    assert fired == expected
    hud.close()


def test_official_ttl_expiry_before_reset_never_emits_official_reset_toast(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    ttl = now + timedelta(seconds=3)
    reset = now + timedelta(seconds=8)
    view = replace(
        _view(pct=50, reset=reset),
        source_5h="oficial",
        reset_source_5h="oficial",
        official_until_5h=ttl,
        quota_identity="a" * 64,
        identity_state="confirmed",
        quota_state="fresh",
        quota_fresh_5h=True,
        official_reset_5h=reset,
        quota_observed_at_5h=now,
        estimated_reset_5h=now + timedelta(seconds=20),
    )
    clock = [now]
    hud = HudWindow(view_loader=lambda _n: {"claude": view}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    got = []
    hud.renewed.connect(got.append)
    clock[0] = ttl
    hud.tick()
    assert "estimado · desatualizado" in hud.rows["claude"].text_5h.text()
    clock[0] = reset
    hud.tick()
    hud.tick()
    assert got == []
    hud.close()


@pytest.mark.parametrize("unsafe", ["disconnect", "tabs", "identity", "conflict"])
def test_unsafe_identity_transition_at_reset_boundary_never_toasts(qapp, unsafe):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    boundary = now + timedelta(seconds=5)
    account_a = replace(
        _view(pct=40, reset=boundary),
        source_5h="oficial",
        reset_source_5h="oficial",
        official_until_5h=boundary,
        quota_identity="a" * 64,
        identity_state="confirmed",
        quota_state="fresh",
        quota_fresh_5h=True,
        official_reset_5h=boundary,
        quota_observed_at_5h=now,
    )
    account_b = replace(account_a, quota_identity="b" * 64)
    clock = [now]
    current = [account_a]
    hud = HudWindow(view_loader=lambda _n: {"claude": current[0]}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    got = []
    hud.renewed.connect(got.append)
    clock[0] = boundary
    if unsafe == "disconnect":
        hud.on_update("conn", {"clients": 0})
    elif unsafe == "tabs":
        hud.on_update("status", {"tabs": {"claude": True, "claude_count": 2}})
    elif unsafe == "conflict":
        current[0] = replace(
            account_a,
            identity_state="ambiguous",
            quota_state="ambiguous",
            source_5h="estimado · contas ambíguas",
            quota_fresh_5h=False,
            official_reset_5h=None,
            renewal_boundary_5h=None,
        )
        hud.refresh()
    else:
        current[0] = account_b
        hud.refresh()
    hud.tick()
    assert got == []
    if unsafe == "disconnect":
        hud.on_update("conn", {"clients": 1})
        hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
        hud.tick()
        assert got == []
    hud.close()


@pytest.mark.parametrize("unsafe", ["disconnect", "tabs", "conflict"])
def test_unsafe_generation_does_not_rearm_without_new_observation_before_reset(qapp, unsafe):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    reset = now + timedelta(seconds=10)
    clock = [now]
    quota = [
        replace(
            _view(pct=40, reset=reset),
            source_5h="oficial",
            reset_source_5h="oficial",
            quota_identity="a" * 64,
            identity_state="confirmed",
            quota_state="fresh",
            quota_fresh_5h=True,
            official_reset_5h=reset,
            quota_observed_at_5h=now,
        )
    ]
    hud = HudWindow(view_loader=lambda _n: {"claude": quota[0]}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    fired = []
    hud.renewed.connect(fired.append)
    clock[0] = now + timedelta(seconds=1)
    if unsafe == "disconnect":
        hud.on_update("conn", {"clients": 0})
        clock[0] += timedelta(seconds=1)
        hud.on_update("conn", {"clients": 1})
        hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    elif unsafe == "tabs":
        hud.on_update("status", {"tabs": {"claude": True, "claude_count": 2}})
        clock[0] += timedelta(seconds=1)
        hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    else:
        quota[0] = replace(
            quota[0],
            identity_state="ambiguous",
            quota_state="ambiguous",
            quota_fresh_5h=False,
            official_reset_5h=None,
        )
        hud.refresh()
        clock[0] += timedelta(seconds=1)
        quota[0] = replace(
            quota[0],
            identity_state="confirmed",
            quota_state="fresh",
            quota_fresh_5h=True,
            official_reset_5h=reset,
        )
        hud.refresh()
    clock[0] = reset
    hud.tick()
    hud.tick()
    assert fired == []
    hud.close()


def test_new_valid_observation_after_invalidation_arms_new_generation_once(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    reset = now + timedelta(seconds=10)
    clock = [now]
    quota = [
        replace(
            _view(pct=40, reset=reset),
            source_5h="oficial",
            reset_source_5h="oficial",
            quota_identity="a" * 64,
            identity_state="confirmed",
            quota_state="fresh",
            quota_fresh_5h=True,
            official_reset_5h=reset,
            quota_observed_at_5h=now,
        )
    ]
    hud = HudWindow(view_loader=lambda _n: {"claude": quota[0]}, clock=lambda: clock[0])
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    fired = []
    hud.renewed.connect(fired.append)
    clock[0] = now + timedelta(seconds=1)
    hud.on_update("conn", {"clients": 0})
    clock[0] += timedelta(seconds=1)
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    hud.tick()
    assert fired == []
    clock[0] += timedelta(seconds=1)
    quota[0] = replace(quota[0], quota_observed_at_5h=clock[0])
    hud.refresh()
    clock[0] = reset
    hud.tick()
    hud.tick()
    assert fired == ["claude"]
    hud.close()


@pytest.mark.parametrize("compact", [False, True])
def test_model_quota_ttl_and_reset_expire_independently_on_tick(qapp, compact):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    opus = ModelQuotaView(
        "opus", 35, now + timedelta(minutes=2), now - timedelta(minutes=9), "oficial"
    )
    sonnet = ModelQuotaView(
        "sonnet", 62, now + timedelta(minutes=2), now - timedelta(minutes=1), "oficial"
    )
    base = replace(
        _view(pct=20),
        source_5h="oficial",
        official_until_5h=now + timedelta(minutes=20),
        quota_identity="a" * 64,
        identity_state="confirmed",
        model_quotas=(opus, sonnet),
        model_quota="opus: 35% oficial · sonnet: 62% oficial",
    )
    clock = [now]
    calls = []
    hud = HudWindow(
        view_loader=lambda t: calls.append(t) or {"claude": base},
        clock=lambda: clock[0],
        compact=compact,
    )
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    initial = len(calls)

    def detail():
        return (
            hud.compact_rows["claude"].toolTip()
            if compact
            else hud.rows["claude"].model_label.text()
        )

    assert "opus: 35% oficial" in detail() and "sonnet: 62% oficial" in detail()
    clock[0] = now + timedelta(minutes=1) - timedelta(microseconds=1)
    hud.tick()
    assert "opus: 35% oficial" in detail()
    clock[0] = now + timedelta(minutes=1)
    hud.tick()
    assert "opus: 35% snapshot desatualizado · origem oficial" in detail()
    assert "sonnet: 62% oficial" in detail()
    clock[0] = now + timedelta(minutes=1, microseconds=1)
    hud.tick()
    assert "opus: 35% snapshot desatualizado · origem oficial" in detail()
    assert "sonnet: 62% oficial" in detail()
    clock[0] = now + timedelta(minutes=2)
    hud.tick()
    assert "sonnet: 62% snapshot desatualizado · origem oficial" in detail()
    clock[0] = now + timedelta(minutes=2, microseconds=1)
    hud.tick()
    assert "sonnet: 62% snapshot desatualizado · origem oficial" in detail()
    assert hud.rows["claude"].bar_5h.value() == 20
    assert len(calls) == initial
    hud.close()


@pytest.mark.parametrize("compact", [False, True])
def test_fresh_aggregate_stale_model_origins_in_expanded_and_compact_tooltips(qapp, compact):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    stale_model = ModelQuotaView(
        "opus",
        71,
        now - timedelta(seconds=1),
        now - timedelta(minutes=9),
        "snapshot desatualizado · origem oficial",
        is_fresh=False,
    )
    view = replace(
        _view(pct=25),
        source_5h="oficial",
        official_until_5h=now + timedelta(minutes=5),
        quota_identity="a" * 64,
        identity_state="confirmed",
        model_quotas=(stale_model,),
        model_quota="opus: 71% snapshot desatualizado · origem oficial",
    )
    hud = HudWindow(view_loader=lambda _n: {"claude": view}, clock=lambda: now, compact=compact)
    hud.on_update("conn", {"clients": 1})
    hud.on_update("status", {"tabs": {"claude": True, "claude_count": 1}})
    row = hud.rows["claude"]
    assert "25% · oficial" in row.text_5h.text()
    assert "71% snapshot desatualizado · origem oficial" in row.model_label.text()
    assert "71% snapshot desatualizado · origem oficial" in row.model_label.toolTip()
    if compact:
        assert "71% snapshot desatualizado · origem oficial" in hud.compact_rows["claude"].toolTip()
    hud.close()
