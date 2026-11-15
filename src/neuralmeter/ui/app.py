"""Ponto de entrada da UI: HUD + servidor WS."""

import signal
import sys
from datetime import UTC, datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from neuralmeter.antigravity import (
    AntigravitySnapshot,
    antigravity_endpoint_identity,
    read_antigravity_quota,
)
from neuralmeter.claude_cli import CliSnapshot, QuotaPoller, credential_identity
from neuralmeter.codex_cli import CodexSnapshot, codex_auth_metadata_identity, read_codex_quota
from neuralmeter.db import connect_db
from neuralmeter.engine import DEFAULT_CHATGPT_LIMITS, calibrated_chatgpt_limits
from neuralmeter.privacy import apply_retention
from neuralmeter.server import start_ws_server
from neuralmeter.ui.collector_lifecycle import CollectorLifecycle
from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.icons import load_app_icon
from neuralmeter.ui.identity_probe import IdentityProbeGate, IdentityProbeManager
from neuralmeter.ui.quota_delivery import QuotaDeliveryGate
from neuralmeter.ui.settings import (
    clamp_opacity,
    ensure_privacy_salt,
    load_settings,
    parse_model_limits,
    parse_privacy_settings,
    parse_quota_visibility,
    parse_weight_params,
    save_settings,
)
from neuralmeter.ui.tray import build_tray
from neuralmeter.ui.view_model import load_hud_view


def run_app(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv if argv is None else ["neuralmeter", *argv])
    app.setQuitOnLastWindowClosed(False)
    app_icon = load_app_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    settings = load_settings()
    db = connect_db()
    bridge = UpdateBridge()
    cli_enabled = bool(settings.get("claude_cli_enabled", False))
    codex_enabled = bool(settings.get("codex_cli_enabled", False))
    antigravity_enabled = bool(settings.get("antigravity_enabled", False))
    quota_visibility = parse_quota_visibility(settings.get("quota_visibility"))
    collectors = CollectorLifecycle()
    source_local_identity: dict[str, str | None] = {}

    def route_snapshot(source, snapshot) -> None:
        local_identity = getattr(snapshot, "local_identity", None)
        if source == "claude":
            local_identity = getattr(snapshot, "identity", None)
        if local_identity is not None:
            source_local_identity[source] = local_identity
        if source == "claude":
            publish_cli(snapshot)
        elif source == "codex":
            publish_codex(snapshot)
        elif source == "antigravity":
            publish_antigravity(snapshot)

    delivery = QuotaDeliveryGate(route_snapshot)

    def start_collector(source: str, factory) -> int:
        delivery_generation = delivery.enable(source)
        callback = delivery.callback(source, delivery_generation)
        generation = collectors.start(source, factory, callback)
        identity_probes.cancel(source, generation=generation)
        return generation

    def stop_collector(source: str) -> None:
        delivery.invalidate(source)
        generation = collectors.stop(source)
        identity_probes.cancel(source, generation=generation)

    def start_claude_poller() -> None:
        start_collector("claude", lambda callback: QuotaPoller(callback))

    def start_codex_poller() -> None:
        start_collector(
            "codex",
            lambda callback: QuotaPoller(callback, fetch=read_codex_quota, pass_cancel_event=True),
        )

    def start_antigravity_poller() -> None:
        start_collector(
            "antigravity",
            lambda callback: QuotaPoller(
                callback, fetch=read_antigravity_quota, pass_cancel_event=True
            ),
        )

    def publish_cli(snapshot: CliSnapshot) -> None:
        bridge.updated.emit(
            "cli",
            {
                "status": snapshot.status,
                "observed_at": snapshot.observed_at,
                "five_hour": {
                    "percent": snapshot.five_hour.percent,
                    "reset_at": snapshot.five_hour.reset_at,
                },
                "seven_day": {
                    "percent": snapshot.seven_day.percent,
                    "reset_at": snapshot.seven_day.reset_at,
                },
            },
        )

    def set_cli_enabled(value: bool) -> None:
        nonlocal cli_enabled
        cli_enabled = bool(value)
        settings["claude_cli_enabled"] = cli_enabled
        save_settings(settings)
        if cli_enabled:
            hud.set_cli_status({"status": "unknown"})
            start_claude_poller()
        else:
            stop_collector("claude")
            publish_cli(CliSnapshot("disconnected"))

    def publish_codex(snapshot: CodexSnapshot) -> None:
        bridge.updated.emit(
            "codex",
            {
                "status": snapshot.status,
                "windows": [
                    {
                        "scope": window.scope,
                        "limit_id": window.limit_id,
                        "limit_name": window.limit_name,
                        "percent": window.percent,
                        "reset_at": window.reset_at,
                        "window_minutes": window.window_minutes,
                    }
                    for window in snapshot.windows
                ],
                "observed_at": snapshot.observed_at,
                "coverage_partial": snapshot.coverage_partial,
            },
        )

    def set_codex_enabled(value: bool) -> None:
        nonlocal codex_enabled
        codex_enabled = bool(value)
        settings["codex_cli_enabled"] = codex_enabled
        save_settings(settings)
        if codex_enabled:
            hud.set_codex_status({"status": "unknown"})
            start_codex_poller()
        else:
            stop_collector("codex")
            publish_codex(CodexSnapshot("not_connected"))

    def publish_antigravity(snapshot: AntigravitySnapshot) -> None:
        bridge.updated.emit(
            "antigravity",
            {
                "status": snapshot.status,
                "observed_at": snapshot.observed_at,
                "coverage_partial": snapshot.coverage_partial,
                "identity_known": snapshot.identity is not None,
                "buckets": [
                    {
                        "bucket_id": bucket.bucket_id,
                        "model": bucket.model,
                        "window": bucket.window,
                        "used_percent": bucket.used_percent,
                        "reset_at": bucket.reset_at,
                    }
                    for bucket in snapshot.buckets
                ],
            },
        )

    def set_antigravity_enabled(value: bool) -> None:
        nonlocal antigravity_enabled
        antigravity_enabled = bool(value)
        settings["antigravity_enabled"] = antigravity_enabled
        save_settings(settings)
        if antigravity_enabled:
            hud.set_antigravity_status({"status": "unknown"})
            start_antigravity_poller()
        else:
            stop_collector("antigravity")
            publish_antigravity(AntigravitySnapshot("not_connected"))

    def on_moved(x: int, y: int) -> None:
        settings["hud_pos"] = [x, y]
        save_settings(settings)

    def on_compact_changed(value: bool) -> None:
        settings["compact"] = value
        save_settings(settings)

    def on_opacity_changed(value: int) -> None:
        settings["opacity"] = value
        save_settings(settings)

    def on_click_through_changed(value: bool) -> None:
        settings["click_through"] = value
        save_settings(settings)

    identity_probes = IdentityProbeManager(
        {
            "claude": credential_identity,
            "codex": codex_auth_metadata_identity,
            "antigravity": antigravity_endpoint_identity,
        }
    )

    def on_identity_probe(source: str, epoch: int, generation: int, current: str | None) -> None:
        if not collectors.is_current(source, generation):
            return
        if not identity_probes.is_current(source, epoch, generation):
            return
        previous = source_local_identity.get(source)
        source_local_identity[source] = current
        if previous is None or current == previous:
            return
        if source == "claude":
            hud.set_cli_status({"status": "identity_changed"})
            start_claude_poller()
        elif source == "codex":
            hud.set_codex_status({"status": "identity_changed"})
            start_codex_poller()
        else:
            hud.set_antigravity_status({"status": "identity_changed"})
            start_antigravity_poller()

    identity_probe_gate = IdentityProbeGate(
        identity_probes, collectors.generation, on_identity_probe
    )
    identity_probes.result.connect(identity_probe_gate.consume)

    def schedule_identity_probes() -> None:
        for source, enabled in (
            ("claude", cli_enabled),
            ("codex", codex_enabled),
            ("antigravity", antigravity_enabled),
        ):
            if enabled and collectors.is_current(source, collectors.generation(source)):
                identity_probes.schedule(source, collectors.generation(source))

    def model_limits() -> dict:
        return {
            **DEFAULT_CHATGPT_LIMITS,
            **calibrated_chatgpt_limits(db),
            **parse_model_limits(settings.get("chatgpt_model_limits")),
        }

    # Sem tray, click-through prenderia o usuario: forca desligado.
    tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
    hud = HudWindow(
        on_moved=on_moved,
        view_loader=lambda now: load_hud_view(db, now, chatgpt_limits=model_limits()),
        compact=bool(settings.get("compact")),
        on_compact_changed=on_compact_changed,
        opacity=clamp_opacity(settings.get("opacity")),
        click_through=bool(settings.get("click_through")) and tray_ok,
    )
    bridge.updated.connect(hud.on_update)
    hud.set_quota_visibility(quota_visibility, resize=False)
    hud.cli_status.setText(
        "Claude CLI: aguardando opt-in" if not cli_enabled else "Claude CLI: verificando"
    )
    hud.cli_status.setVisible(cli_enabled)
    if cli_enabled:
        start_claude_poller()
    if codex_enabled:
        hud.set_codex_status({"status": "unknown"})
        start_codex_poller()
    if antigravity_enabled:
        hud.set_antigravity_status({"status": "unknown"})
        start_antigravity_poller()
    hud.opacity_changed.connect(on_opacity_changed)
    hud.click_through_changed.connect(on_click_through_changed)
    hud.refresh()
    hud.apply_saved_position(settings.get("hud_pos"))

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(500)

    if ensure_privacy_salt(settings):
        save_settings(settings)
    privacy = parse_privacy_settings(settings)
    apply_retention(db, datetime.now(UTC), privacy.retention_days)
    retention_timer = QTimer()
    retention_timer.timeout.connect(
        lambda: apply_retention(db, datetime.now(UTC), privacy.retention_days)
    )
    retention_timer.start(6 * 60 * 60 * 1000)
    identity_timer = QTimer()
    identity_timer.timeout.connect(schedule_identity_probes)
    identity_timer.start(2500)
    schedule_identity_probes()

    server = start_ws_server(
        db,
        on_update=bridge.emit_update,
        weight_params=parse_weight_params(settings.get("claude_weight")),
        privacy=privacy,
    )

    def on_privacy_changed(value: bool) -> None:
        nonlocal privacy
        raw = settings.get("privacy")
        settings["privacy"] = {
            **(raw if isinstance(raw, dict) else {}),
            "hash_conversation_ids": value,
        }
        save_settings(settings)
        privacy = parse_privacy_settings(settings)
        server.set_privacy(privacy)

    def quit_app() -> None:
        identity_timer.stop()
        identity_probes.stop()
        collectors.shutdown()
        server.stop()
        app.quit()

    def on_notify_changed(value: bool) -> None:
        settings["notify_renewal"] = value
        save_settings(settings)

    def on_quota_visibility_changed(value: dict[str, bool]) -> None:
        nonlocal quota_visibility
        quota_visibility = parse_quota_visibility(value)
        settings["quota_visibility"] = quota_visibility
        save_settings(settings)
        hud.set_quota_visibility(quota_visibility, resize=True)

    tray = build_tray(
        hud,
        quit_app,
        conn=db,
        notify_renewal=bool(settings.get("notify_renewal", True)),
        on_notify_changed=on_notify_changed,
        privacy_hash=privacy.hash_conversation_ids,
        on_privacy_changed=on_privacy_changed,
        cli_enabled=cli_enabled,
        on_cli_changed=set_cli_enabled,
        codex_enabled=codex_enabled,
        on_codex_changed=set_codex_enabled,
        antigravity_enabled=antigravity_enabled,
        on_antigravity_changed=set_antigravity_enabled,
        quota_visibility=quota_visibility,
        on_quota_visibility_changed=on_quota_visibility_changed,
    )
    if tray_ok:
        tray.show()
    hud.show()
    try:
        return app.exec()
    finally:
        identity_timer.stop()
        identity_probes.stop()
        identity_probes.join(timeout=5)
        collectors.shutdown()
        collectors.join_all(timeout=5)
        server.stop()
