"""Functional Chrome E2E and visual matrix using isolated synthetic CLI fixtures."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import struct
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright

from neuralmeter.antigravity_usage import read_antigravity_usage
from neuralmeter.claude_cli import CliSnapshot, QuotaWindow
from neuralmeter.codex_cli import CodexWindow
from neuralmeter.web import LocalDashboard, _SourceService

THEMES = ("graphite", "light", "aurora")
VIEWPORTS = ((800, 600), (1440, 900))
DPRS = (1, 1.5, 2)
PRIVATE_FIXTURE = "synthetic-private-prompt-sentinel-never-display"
SCREENSHOT_CONTEXT: dict = {}


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as image:
        header = image.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"screenshot is not PNG: {path.name}")
    return struct.unpack(">II", header[16:24])


def _screenshot(page: Page, output: Path, metadata: dict, records: list[dict]) -> None:
    page.evaluate("window.scrollTo(0, 0)")
    page.evaluate(
        """() => document.querySelectorAll('.rail nav, .table-wrap').forEach((element) => {
          element.scrollLeft = 0;
        })"""
    )
    page.wait_for_timeout(35)
    page.screenshot(path=str(output), animations="disabled", full_page=False)
    width_px, height_px = _png_dimensions(output)
    expected_raster = metadata.get("target_raster_px")
    if expected_raster and (width_px, height_px) != tuple(expected_raster):
        raise AssertionError(
            f"PNG size mismatch: expected {expected_raster}, got {[width_px, height_px]}"
        )
    entry = {
        **SCREENSHOT_CONTEXT,
        **metadata,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "path": str(output.resolve()),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "png_width_px": width_px,
        "png_height_px": height_px,
    }
    records.append(entry)


def _fixture_overlay(page: Page) -> None:
    page.evaluate(
        """() => {
          const badge = document.createElement('div');
          badge.id = 'e2e-fixture-watermark';
          badge.textContent = 'FIXTURE SINTÉTICA — sem dados reais';
          badge.setAttribute('aria-label', badge.textContent);
          badge.style.cssText = 'position:fixed;z-index:2147483647;top:7px;right:8px;'
            + 'padding:4px 8px;border-radius:6px;background:#ffe08a;color:#241b00;'
            + 'font:600 11px/1.2 system-ui;box-shadow:0 1px 5px #0005;pointer-events:none';
          document.body.append(badge);
        }"""
    )


def _instrument_page(
    page: Page, origin: str, report: dict, private_values: tuple[str, ...]
) -> None:
    def request_seen(request) -> None:
        parsed = urlsplit(request.url)
        if parsed.scheme not in {"http", "https"}:
            return
        if f"{parsed.scheme}://{parsed.netloc}" != origin:
            report["external_requests"].append(f"{parsed.scheme}://{parsed.netloc}")
        report["requests"].append({"method": request.method, "path": parsed.path})

    def response_seen(response) -> None:
        parsed = urlsplit(response.url)
        if parsed.scheme not in {"http", "https"} or f"{parsed.scheme}://{parsed.netloc}" != origin:
            return
        report["responses"].append({"path": parsed.path, "status": response.status})
        if response.status >= 400:
            report["bad_responses"].append({"path": parsed.path, "status": response.status})

    page.on("request", request_seen)
    page.on("response", response_seen)
    page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
    page.on(
        "console",
        lambda message: (
            report["console_messages"].append({"type": message.type, "message": message.text[:240]})
            if message.type in {"error", "warning"}
            else None
        ),
    )

    def request_failed(request) -> None:
        target = (
            "expected_lifecycle_aborts"
            if request.failure == "net::ERR_ABORTED"
            else "failed_requests"
        )
        report[target].append(
            {
                "path": urlsplit(request.url).path,
                "failure": request.failure or "unknown",
                "navigation": request.is_navigation_request(),
            }
        )

    page.on("requestfailed", request_failed)


def _wait_page(page: Page, origin: str) -> None:
    page.goto(origin, wait_until="domcontentloaded")
    page.locator('#source-cards[aria-busy="false"]').wait_for(timeout=6000)


def _wait_until(predicate, label: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {label}")


def _rows(page: Page) -> dict[str, list[str]]:
    result = {}
    for row in page.locator("#model-rows tr").all():
        cells = row.locator("td").all_text_contents()
        if cells and cells[0] not in {
            "",
            "Nenhuma contagem com horário conhecido neste período.",
            "Autorize Antigravity CLI para ler histórico local.",
        }:
            result[cells[0]] = cells
    return result


def _contrast_audit(page: Page) -> dict:
    return page.evaluate(
        """() => {
          const luminance = (color) => {
            let values;
            if (color.startsWith('#')) {
              const hex = color.slice(1);
              const expanded = hex.length === 3 ? [...hex].map((v) => v + v).join('') : hex;
              values = [0, 2, 4].map((i) => parseInt(expanded.slice(i, i + 2), 16));
            } else {
              values = color.match(/[\\d.]+/g)?.slice(0, 3).map(Number);
            }
            if (!values || values.length !== 3) return null;
            const linear = values.map((v) => {
              const c = v / 255;
              return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
          };
          const ratio = (fg, bg) => {
            const a = luminance(fg), b = luminance(bg);
            if (a === null || b === null) return null;
            return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
          };
          const root = getComputedStyle(document.documentElement);
          const pageBg = root.getPropertyValue('--page').trim();
          const surface = root.getPropertyValue('--surface').trim();
          const lede = getComputedStyle(document.querySelector('.lede')).color;
          const th = getComputedStyle(document.querySelector('th')).color;
          const status = getComputedStyle(document.querySelector('.status')).color;
          return {
            lede: ratio(lede, pageBg),
            tableHeader: ratio(th, surface),
            status: ratio(status, surface)
          };
        }"""
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    output_dir = args.artifact_dir or (
        Path.home() / "AppData" / "Local" / "Temp" / "opencode" / f"web-preview-B-{stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    screenshots: list[dict] = []
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "fixture_mode": ("synthetic temp settings/transcript; no provider or user-profile access"),
        "browser_profile": "ephemeral Playwright context; not personal Chrome profile",
        "scale_method": (
            "Inject documentElement.style.zoom; CSS zoom is not browser-toolbar zoom or "
            "physical Windows DPI; CSS viewport reduced by scale and DPR selected for target raster"
        ),
        "screenshots": screenshots,
        "requests": [],
        "responses": [],
        "bad_responses": [],
        "external_requests": [],
        "failed_requests": [],
        "expected_lifecycle_aborts": [],
        "page_errors": [],
        "console_messages": [],
        "privacy_leaks": [],
        "assertions": [],
    }
    manifest_path = output_dir / "preview-manifest.json"

    def preserve_partial_manifest() -> None:
        if manifest_path.exists():
            return
        report.setdefault("status", "INCOMPLETE")
        report["screenshot_count"] = len(screenshots)
        report["network_external_count"] = len(report["external_requests"])
        report["expected_lifecycle_abort_count"] = len(report["expected_lifecycle_aborts"])
        report["page_error_count"] = len(report["page_errors"])
        report["console_warning_or_error_count"] = len(report["console_messages"])
        report["source_read_counts"] = dict(call_counts)
        report["identity_probe_counts"] = dict(probe_counts)
        current_dashboard = dashboard_ref[0]
        if current_dashboard is not None:
            state = current_dashboard.service.get_snapshot()["sources"]
            report["debug_source_states"] = {
                name: {
                    "enabled": worker.enabled,
                    "generation": worker.generation,
                    "refresh_requested": worker.refresh_requested,
                    "cooldown_active": worker.rate_limited_until > fixture_clock[0],
                    "status": state[name]["status"],
                    "quota_count": len(state[name]["quotas"]),
                }
                for name, worker in current_dashboard.service.workers.items()
            }
        manifest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    atexit.register(preserve_partial_manifest)
    original_readers = _SourceService.__dict__["_readers"]
    original_identity_readers = _SourceService.__dict__["_identity_readers"]
    fixture_lock = threading.RLock()
    call_counts = {"claude": 0, "codex": 0, "antigravity_cli": 0}
    probe_counts = {"claude": 0, "codex": 0}
    fixture_clock = [time.monotonic()]
    dashboard_ref = [None]
    identities = {"claude": "a" * 64, "codex": "c" * 64}
    claude_mode = {"reset": "future"}
    first_claude_rate_limit = {"pending": True}
    barriers = {
        source: {"next": False, "entered": threading.Event(), "release": threading.Event()}
        for source in call_counts
    }
    private_values = (PRIVATE_FIXTURE, "synthetic-private-tool-args-sentinel")

    with TemporaryDirectory(prefix="neuralmeter-browser-B-") as temporary:
        root = Path(temporary)
        data_dir = root / "settings"
        data_dir.mkdir()
        (data_dir / "settings.json").write_text(
            json.dumps(
                {
                    "unrelated_legacy_setting": "preserve-me",
                    "claude_cli_enabled": False,
                    "codex_cli_enabled": False,
                    "antigravity_cli_usage_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        history_root = root / "synthetic-antigravity-history"
        history_file = (
            history_root / "session-001" / ".system_generated" / "logs" / "transcript.jsonl"
        )
        history_root.mkdir(parents=True)

        def write_history(version: str) -> None:
            now = datetime.now(UTC)
            flash_output = 17 if version == "A" else 99
            rows = [
                {
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-2.5-flash",
                    "input_tokens": 0,
                    "output_tokens": flash_output,
                    "cache_read_tokens": 3,
                    "cache_write_tokens": 6,
                    "timestamp": now.isoformat(),
                },
                {
                    "type": "USER_INPUT",
                    "model": "gemini-2.5-pro",
                    "content": PRIVATE_FIXTURE,
                    "timestamp": now.isoformat(),
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-2.0-flash",
                    "thinking": "synthetic derived output",
                    "tool_calls": [{"args": {"query": "synthetic-private-tool-args-sentinel"}}],
                    "timestamp": now.isoformat(),
                },
            ]
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

        write_history("A")

        def maybe_block(source: str, result):
            barrier = barriers[source]
            with fixture_lock:
                should_block = barrier["next"]
                if should_block:
                    barrier["next"] = False
                    barrier["entered"].clear()
                    barrier["release"].clear()
            if should_block:
                barrier["entered"].set()
                if not barrier["release"].wait(8):
                    raise TimeoutError("synthetic E2E barrier timed out")
            return result

        def claude_reader(_cancel):
            with fixture_lock:
                call_counts["claude"] += 1
                identity = identities["claude"]
                mode = claude_mode["reset"]
                use_rate_limit = first_claude_rate_limit["pending"]
                if use_rate_limit:
                    first_claude_rate_limit["pending"] = False
            if use_rate_limit:
                result = CliSnapshot("rate_limited", retry_after=60, identity=identity)
            else:
                current = datetime.now(UTC)
                five_reset = {
                    "future": current + timedelta(hours=2),
                    "missing": None,
                    "expired": current - timedelta(minutes=1),
                }[mode]
                result = CliSnapshot(
                    "available",
                    QuotaWindow(81 if identity == "b" * 64 else 42, five_reset),
                    QuotaWindow(65, None),
                    current,
                    identity=identity,
                    auth_status="authenticated",
                )
            return maybe_block("claude", result)

        def codex_reader(_cancel):
            with fixture_lock:
                call_counts["codex"] += 1
            now = datetime.now(UTC)
            result = type(
                "SyntheticCodex",
                (),
                {
                    "status": "available",
                    "windows": (
                        CodexWindow("primary", 23, now + timedelta(hours=3)),
                        CodexWindow("secondary", 0, None),
                    ),
                    "observed_at": now,
                    "retry_after": None,
                    "identity": "d" * 64,
                    "local_identity": identities["codex"],
                    "coverage_partial": False,
                },
            )()
            return maybe_block("codex", result)

        def agy_reader(cancel_event):
            with fixture_lock:
                call_counts["antigravity_cli"] += 1
            now = datetime.now(UTC)
            result = read_antigravity_usage(
                consent_granted=True,
                roots=(history_root,),
                cancel_event=cancel_event,
                max_seconds=2,
                now=now,
                timezone=UTC,
            )
            return maybe_block("antigravity_cli", result)

        def fake_readers():
            return {"claude": claude_reader, "codex": codex_reader, "antigravity_cli": agy_reader}

        def fake_identity_readers():
            def probe(source):
                with fixture_lock:
                    probe_counts[source] += 1
                    return identities[source]

            return {"claude": lambda: probe("claude"), "codex": lambda: probe("codex")}

        _SourceService._readers = staticmethod(fake_readers)
        _SourceService._identity_readers = staticmethod(fake_identity_readers)
        dashboard = LocalDashboard(port=0, data_dir=data_dir)
        dashboard_ref[0] = dashboard
        dashboard.service.monotonic = lambda: fixture_clock[0]
        for worker in dashboard.service.workers.values():
            worker.monotonic = lambda: fixture_clock[0]

        def advance_source_read_floor(source: str) -> None:
            worker = dashboard.service.workers[source]
            with worker.condition:
                fixture_clock[0] = (
                    max(fixture_clock[0], worker.rate_limited_until, worker.next_read_at) + 0.01
                )

        dashboard.start(open_browser=False)
        report["origin"] = dashboard.url.rstrip("/")
        report["base_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        SCREENSHOT_CONTEXT.update(
            {
                "source_base": report["base_commit"],
                "fixture_mode": report["fixture_mode"],
                "browser": "Chrome headless",
                "source_file_manifest_sha256": os.environ.get("NEURALMETER_SOURCE_SNAPSHOT_SHA256"),
            }
        )
        report["source_file_manifest_sha256"] = SCREENSHOT_CONTEXT["source_file_manifest_sha256"]
        report["fixture_paths"] = {
            "settings": str((data_dir / "settings.json").resolve()),
            "antigravity_transcript": str(history_file.resolve()),
        }
        report["settings_data_dir_is_temporary"] = str(data_dir).startswith(str(root))
        report["fixture_transcript_is_temporary"] = str(history_file).startswith(str(root))

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                report["browser"] = "Chrome headless"
                report["browser_version"] = browser.version
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900}, device_scale_factor=1
                )
                page = context.new_page()
                _instrument_page(page, report["origin"], report, private_values)
                _wait_page(page, dashboard.url)
                _fixture_overlay(page)
                assert call_counts == {"claude": 0, "codex": 0, "antigravity_cli": 0}
                assert probe_counts == {"claude": 0, "codex": 0}
                assert "0%" not in page.locator("body").inner_text()
                assert page.locator("#source-cards article").count() == 3
                report["assertions"].append(
                    "opt-in off: no reader/probe; unknown values never 0%; all 3 sources visible"
                )
                with page.expect_response(
                    lambda response: response.url.endswith("/api/refresh"), timeout=5000
                ) as unauthorized_refresh:
                    page.get_by_role("button", name="Atualizar autorizadas").click()
                assert unauthorized_refresh.value.json() == {
                    "accepted": False,
                    "reason": "not_opted_in",
                }
                page.locator("#notice").get_by_text(
                    "Nenhuma fonte autorizada para atualizar."
                ).wait_for(timeout=3000)
                assert call_counts == {"claude": 0, "codex": 0, "antigravity_cli": 0}
                report["assertions"].append(
                    "refresh refusal distinguishes missing opt-in in API and UI without source I/O"
                )
                _screenshot(
                    page,
                    output_dir / "state-unknown.png",
                    {
                        "state": "all sources off; unknown values",
                        "theme": "graphite",
                        "viewport_css_px": [1440, 900],
                        "device_scale_factor": 1,
                        "css_zoom": 1,
                    },
                    screenshots,
                )

                # Theme/period are visual preferences; reload proves persistence and no source I/O.
                page.get_by_label("Tema").select_option("light")
                page.get_by_label("Período").select_option("history")
                page.reload(wait_until="domcontentloaded")
                page.locator('#source-cards[aria-busy="false"]').wait_for(timeout=6000)
                _fixture_overlay(page)
                assert page.locator("html").get_attribute("data-theme") == "light"
                assert page.get_by_label("Período").input_value() == "history"
                assert call_counts == {"claude": 0, "codex": 0, "antigravity_cli": 0}
                report["assertions"].append("theme+period survive reload without enabling readers")
                persisted = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
                assert persisted["unrelated_legacy_setting"] == "preserve-me"

                # Synthetic 429: repeat consent from second tab and refresh must not repoll.
                page.get_by_label("Permitir leitura local: Claude Code").check()
                _wait_until(lambda: call_counts["claude"] == 1, "synthetic Claude 429")
                page.locator("article[aria-label='Claude Code'] .status").filter(
                    has_text="Aguardando limite de consulta"
                ).wait_for(timeout=4000)
                generation_after_consent = dashboard.service.workers["claude"].current_generation()
                second_tab = context.new_page()
                _instrument_page(second_tab, report["origin"], report, private_values)
                _wait_page(second_tab, dashboard.url)
                csrf = second_tab.evaluate(
                    "async () => (await (await fetch('/api/bootstrap')).json()).csrf"
                )
                for _ in range(2):
                    result = second_tab.evaluate(
                        """async (csrf) => {
                          const response = await fetch('/api/consent', {
                            method: 'POST', credentials: 'same-origin',
                            headers: {'Content-Type':'application/json','X-NeuralMeter-CSRF':csrf},
                            body: JSON.stringify({source:'claude', enabled:true})
                          }); return response.status;
                        }""",
                        csrf,
                    )
                    assert result == 200
                assert (
                    dashboard.service.workers["claude"].current_generation()
                    == generation_after_consent
                )
                second_tab.close()
                with page.expect_response(
                    lambda response: response.url.endswith("/api/refresh"), timeout=5000
                ) as cooldown_response:
                    page.get_by_role("button", name="Atualizar autorizadas").click()
                assert cooldown_response.value.json() == {
                    "accepted": False,
                    "reason": "cooldown",
                }
                page.locator("#notice").get_by_text(
                    "Aguarde o intervalo mínimo antes de atualizar novamente."
                ).wait_for(timeout=3000)
                time.sleep(0.15)
                assert call_counts["claude"] == 1
                advance_source_read_floor("claude")
                page.get_by_role("button", name="Atualizar autorizadas").click()
                _wait_until(
                    lambda: call_counts["claude"] == 2,
                    "synthetic quota after controlled cooldown expiry",
                )
                page.locator("article[aria-label='Claude Code'] .quota-line strong").filter(
                    has_text="42%"
                ).wait_for(timeout=5000)
                quota_card = page.locator("article[aria-label='Claude Code']")
                assert (
                    quota_card.locator(".quota-reset").first.inner_text().startswith("Renova em ")
                )
                assert "Renovação desconhecida" in quota_card.inner_text()
                report["assertions"].append(
                    "two-tab idempotent consent; cooldown refresh suppressed; "
                    "controlled expiry permits read"
                )
                _screenshot(
                    page,
                    output_dir / "state-quota-reset.png",
                    {
                        "state": "Claude synthetic 42%; future reset plus unknown weekly reset",
                        "theme": "light",
                        "viewport_css_px": [1440, 900],
                        "device_scale_factor": 1,
                        "css_zoom": 1,
                    },
                    screenshots,
                )

                claude_mode["reset"] = "expired"
                advance_source_read_floor("claude")
                page.get_by_role("button", name="Atualizar autorizadas").click()
                _wait_until(lambda: call_counts["claude"] >= 3, "synthetic expired reset")
                page.locator("article[aria-label='Claude Code'] .quota-reset").first.filter(
                    has_text="Janela expirada"
                ).wait_for(timeout=5000)
                assert (
                    page.locator(
                        "article[aria-label='Claude Code'] .quota-line strong"
                    ).first.inner_text()
                    == "—"
                )
                assert "0%" not in page.locator("article[aria-label='Claude Code']").inner_text()
                report["assertions"].append(
                    "past reset expires percentage; UI shows unknown, never 0%"
                )
                _screenshot(
                    page,
                    output_dir / "state-reset-expired.png",
                    {
                        "state": "Claude synthetic expired reset; percentage unknown",
                        "theme": "light",
                        "viewport_css_px": [1440, 900],
                        "device_scale_factor": 1,
                        "css_zoom": 1,
                    },
                    screenshots,
                )
                claude_mode["reset"] = "future"
                advance_source_read_floor("claude")
                page.get_by_role("button", name="Atualizar autorizadas").click()
                _wait_until(lambda: call_counts["claude"] >= 4, "restore future synthetic reset")

                # Codex uses only in-memory fake identities/account data.
                page.get_by_label("Permitir leitura local: Codex CLI").check()
                _wait_until(lambda: call_counts["codex"] >= 1, "synthetic Codex read")
                codex_card = page.locator("article[aria-label='Codex CLI']")
                codex_card.get_by_text("Principal", exact=True).wait_for(timeout=5000)
                codex_card.get_by_text("Secundária", exact=True).wait_for(timeout=5000)
                assert "23%" in codex_card.inner_text()
                report["assertions"].append(
                    "Codex synthetic scope labels render; source-specific opt-in only"
                )

                # AGY parser reads canonical temporary fixture only after its checkbox opt-in.
                page.get_by_label("Período").select_option("today")
                page.get_by_label("Permitir leitura local: Antigravity CLI").check()
                page.get_by_role("cell", name="gemini-2.5-flash").wait_for(timeout=6000)
                model_rows = _rows(page)
                assert model_rows["gemini-2.5-flash"][1:7] == ["0", "—", "17", "—", "3", "6"]
                assert model_rows["gemini-2.5-pro"][1] == "—" and model_rows["gemini-2.5-pro"][
                    2
                ].startswith("~")
                assert model_rows["gemini-2.5-pro"][3:5] == ["—", "—"]
                assert model_rows["gemini-2.0-flash"][1:4] == ["—", "—", "—"]
                assert model_rows["gemini-2.0-flash"][4].startswith("~")
                body_text = page.locator("body").inner_text()
                assert PRIVATE_FIXTURE not in body_text
                assert "synthetic-private-tool-args-sentinel" not in body_text
                api_snapshot = page.evaluate(
                    """async () => JSON.stringify(
                      await (await fetch('/api/snapshot', {credentials:'same-origin'})).json()
                    )"""
                )
                assert not any(value in api_snapshot for value in private_values)
                assert (
                    "quota oficial desconhecida"
                    in page.locator("article[aria-label='Antigravity CLI']").inner_text()
                )
                _screenshot(
                    page,
                    output_dir / "state-usage-models.png",
                    {
                        "state": (
                            "synthetic AGY history; measured/derived/input-only/output-only/cache"
                        ),
                        "theme": "light",
                        "viewport_css_px": [1440, 900],
                        "device_scale_factor": 1,
                        "css_zoom": 1,
                    },
                    screenshots,
                )
                for selected_period in ("last_7_days", "history"):
                    page.get_by_label("Período").select_option(selected_period)
                    assert page.get_by_label("Período").input_value() == selected_period
                    assert "gemini-2.5-flash" in page.locator("#model-rows").inner_text()
                page.reload(wait_until="domcontentloaded")
                page.locator('#source-cards[aria-busy="false"]').wait_for(timeout=6000)
                _fixture_overlay(page)
                assert page.get_by_label("Período").input_value() == "history"
                persisted = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
                assert persisted["unrelated_legacy_setting"] == "preserve-me"
                assert PRIVATE_FIXTURE not in json.dumps(persisted)
                report["assertions"].append(
                    "AGY canonical temp fixture; three periods; measured/derived/null/cache; "
                    "DOM/API/settings expose no transcript content"
                )

                # Verify keyboard operation, live region, labels, table scope, and focus.
                page.get_by_label("Tema").focus()
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement.id") == "refresh"
                focus_width = page.evaluate(
                    "parseFloat(getComputedStyle(document.activeElement).outlineWidth)"
                )
                assert focus_width >= 2
                assert page.locator("#source-cards").get_attribute("aria-live") == "polite"
                assert page.locator("table th[scope='col']").count() == 8
                assert page.get_by_label("Permitir leitura local: Claude Code").count() == 1
                assert page.locator("progress[aria-label]").count() >= 1
                report["assertions"].append(
                    "keyboard focus/outline, live region, labelled controls, table and progress"
                )

                # Identity change clears cache before delayed A callback can publish.
                claude_barrier = barriers["claude"]
                with fixture_lock:
                    claude_barrier["next"] = True
                advance_source_read_floor("claude")
                with page.expect_response(
                    lambda response: response.url.endswith("/api/refresh"), timeout=5000
                ) as refresh_response:
                    page.get_by_role("button", name="Atualizar autorizadas").click()
                assert refresh_response.value.status == 200
                assert claude_barrier["entered"].wait(3), (
                    "Claude controlled read did not enter barrier"
                )
                old_identity = identities["claude"]
                identity_generation = dashboard.service.workers["claude"].current_generation()
                identities["claude"] = "b" * 64
                assert (
                    dashboard.service._observe_identity(
                        "claude", identities["claude"], identity_generation
                    )
                    is not None
                )
                immediate = dashboard.service.get_snapshot()["sources"]["claude"]
                assert immediate["quotas"] == []
                page.reload(wait_until="domcontentloaded")
                page.locator('#source-cards[aria-busy="false"]').wait_for(timeout=6000)
                _fixture_overlay(page)
                assert (
                    page.locator(
                        "article[aria-label='Claude Code'] .quota-stack .metric"
                    ).inner_text()
                    == "—"
                )
                claude_barrier["release"].set()
                advance_source_read_floor("claude")
                _wait_until(
                    lambda: any(
                        q.get("percent") == 81
                        for q in dashboard.service.get_snapshot()["sources"]["claude"]["quotas"]
                    ),
                    "new synthetic identity quota",
                    timeout=6,
                )
                page.locator("article[aria-label='Claude Code'] .quota-line strong").filter(
                    has_text="81%"
                ).wait_for(timeout=6000)
                final_claude = dashboard.service.get_snapshot()["sources"]["claude"]
                assert all(q["percent"] != 42 for q in final_claude["quotas"])
                assert old_identity not in json.dumps(final_claude)
                report["assertions"].append(
                    "local A→B probe clears cache; delayed A result fenced; only B data publishes"
                )

                # Disconnect/reconnect while fixture A blocks; old result must lose to fixture B.
                agy_barrier = barriers["antigravity_cli"]
                with fixture_lock:
                    agy_barrier["next"] = True
                advance_source_read_floor("antigravity_cli")
                page.get_by_role("button", name="Atualizar autorizadas").click()
                assert agy_barrier["entered"].wait(4), "AGY controlled read did not enter barrier"
                page.get_by_label("Permitir leitura local: Antigravity CLI").uncheck()
                page.locator("#model-rows").get_by_text(
                    "Autorize Antigravity CLI para ler histórico local."
                ).wait_for(timeout=5000)
                _screenshot(
                    page,
                    output_dir / "state-disconnected.png",
                    {
                        "state": "Antigravity disconnected while prior generation is blocked",
                        "theme": "light",
                        "viewport_css_px": [1440, 900],
                        "device_scale_factor": 1,
                        "css_zoom": 1,
                    },
                    screenshots,
                )
                page.get_by_label("Permitir leitura local: Antigravity CLI").check()
                page.locator("#model-rows").get_by_text(
                    "Nenhuma contagem com horário conhecido neste período."
                ).wait_for(timeout=5000)
                write_history("B")
                advance_source_read_floor("antigravity_cli")
                agy_barrier["release"].set()
                _wait_until(
                    lambda: "99" in page.locator("#model-rows").inner_text(),
                    "new generation history",
                    timeout=7,
                )
                assert "17" not in _rows(page)["gemini-2.5-flash"][3]
                report["assertions"].append(
                    "disconnect clears counts; reconnect fences delayed generation; "
                    "rewritten temporary fixture B wins"
                )

                # Real CSS zoom: keep target raster size; reduce CSS viewport by zoom factor.
                for theme in THEMES:
                    for target_width, target_height in VIEWPORTS:
                        for zoom in DPRS:
                            width = round(target_width / zoom)
                            height = round(target_height / zoom)
                            matrix_context = browser.new_context(
                                viewport={"width": width, "height": height},
                                device_scale_factor=zoom,
                            )
                            matrix_page = matrix_context.new_page()
                            _instrument_page(matrix_page, report["origin"], report, private_values)
                            _wait_page(matrix_page, dashboard.url)
                            matrix_page.get_by_label("Tema").select_option(theme)
                            matrix_page.reload(wait_until="domcontentloaded")
                            matrix_page.locator('#source-cards[aria-busy="false"]').wait_for(
                                timeout=6000
                            )
                            _fixture_overlay(matrix_page)
                            matrix_page.evaluate(
                                "zoom => { document.documentElement.style.zoom = String(zoom); }",
                                zoom,
                            )
                            matrix_page.evaluate(
                                """zoom => {
                                  document.querySelector('#e2e-fixture-watermark').style.zoom =
                                    String(1 / zoom);
                                }""",
                                zoom,
                            )
                            actual_zoom = matrix_page.evaluate(
                                "getComputedStyle(document.documentElement).zoom"
                            )
                            assert float(actual_zoom) == zoom
                            assert matrix_page.locator("#source-cards article").count() == 3
                            assert (
                                matrix_page.locator("#model-rows")
                                .get_by_text("gemini-2.5-flash")
                                .count()
                                == 1
                            )
                            dimensions = matrix_page.evaluate(
                                """() => ({width: innerWidth, height: innerHeight,
                                  dpr: devicePixelRatio,
                                  cssZoom: getComputedStyle(document.documentElement).zoom,
                                  visualViewportScale: visualViewport.scale,
                                  documentWidth: document.documentElement.scrollWidth,
                                  documentClientWidth: document.documentElement.clientWidth,
                                  tableWidth: document.querySelector('.table-wrap').scrollWidth,
                                  tableClientWidth:
                                    document.querySelector('.table-wrap').clientWidth})"""
                            )
                            assert dimensions["dpr"] == zoom
                            assert float(dimensions["cssZoom"]) == zoom
                            assert (
                                dimensions["documentWidth"] <= dimensions["documentClientWidth"]
                            ), dimensions
                            rail_position = matrix_page.locator(".rail").evaluate(
                                "element => getComputedStyle(element).position"
                            )
                            assert rail_position == ("static" if width <= 760 else "fixed"), (
                                target_width,
                                zoom,
                                width,
                                rail_position,
                            )
                            if dimensions["tableWidth"] > dimensions["tableClientWidth"]:
                                table_scrolled = matrix_page.locator(".table-wrap").evaluate(
                                    "element => { element.scrollLeft = element.scrollWidth; "
                                    "return element.scrollLeft; }"
                                )
                                assert table_scrolled > 0
                                matrix_page.locator(".table-wrap").evaluate(
                                    "element => { element.scrollLeft = 0; }"
                                )
                            contrast = _contrast_audit(matrix_page)
                            report.setdefault("contrast_by_theme", {}).setdefault(theme, []).append(
                                contrast
                            )
                            assert all(
                                value is not None and value >= 4.5 for value in contrast.values()
                            ), contrast
                            if target_width == 800 and zoom == 1:
                                assert dimensions["tableWidth"] > dimensions["tableClientWidth"], (
                                    dimensions
                                )
                            interactive = matrix_page.locator("button, select, input, a[href]")
                            for index in range(interactive.count()):
                                element = interactive.nth(index)
                                element.scroll_into_view_if_needed(timeout=3000)
                                rect = element.bounding_box()
                                assert rect is not None
                                visible_width = dimensions["width"] * zoom
                                assert rect["x"] >= -1 and rect["x"] + rect["width"] <= (
                                    visible_width + 1
                                ), (
                                    theme,
                                    target_width,
                                    zoom,
                                    element.get_attribute("aria-label") or element.inner_text(),
                                    rect,
                                    visible_width,
                                )
                            filename = (
                                f"matrix-{theme}-{target_width}x{target_height}-csszoom"
                                f"{str(zoom).replace('.', '_')}-dpr"
                                f"{str(zoom).replace('.', '_')}.png"
                            )
                            _screenshot(
                                matrix_page,
                                output_dir / filename,
                                {
                                    "state": (
                                        "all synthetic sources opted-in; responsive dashboard/table"
                                    ),
                                    "theme": theme,
                                    "target_raster_px": [target_width, target_height],
                                    "actual_viewport_css_px": [width, height],
                                    "device_scale_factor": zoom,
                                    "css_zoom": zoom,
                                    "browser_toolbar_zoom": "not automated; CSS zoom differs",
                                    "actual_viewport": dimensions,
                                    "contrast_ratio": contrast,
                                },
                                screenshots,
                            )
                            matrix_context.close()
                report["assertions"].append(
                    "18 bundled-UI screenshots; 3 themes × 2 rasters; actual CSS zoom 1, 1.5, 2"
                )

                # UI shutdown is local API action only; no OS process is terminated by E2E.
                page.get_by_role("button", name="Encerrar NeuralMeter").click()
                page.get_by_role("status").filter(has_text="NeuralMeter será encerrado").wait_for(
                    timeout=3000
                )
                report["assertions"].append(
                    "same-origin CSRF shutdown acknowledged; test cleanup stops workers"
                )
                context.close()
                browser.close()
        finally:
            for barrier in barriers.values():
                barrier["release"].set()
            dashboard.close()
            _SourceService._readers = original_readers
            _SourceService._identity_readers = original_identity_readers

        report["source_read_counts"] = dict(call_counts)
        report["identity_probe_counts"] = dict(probe_counts)
        report["owned_workers_joined"] = all(
            not worker.thread.is_alive() for worker in dashboard.service.workers.values()
        )
        report["identity_monitor_joined"] = not dashboard.service.identity_thread.is_alive()
        report["settings_legacy_value_preserved"] = (
            json.loads((data_dir / "settings.json").read_text(encoding="utf-8")).get(
                "unrelated_legacy_setting"
            )
            == "preserve-me"
        )
        report["screenshot_count"] = len(screenshots)
        report["network_external_count"] = len(report["external_requests"])
        report["expected_lifecycle_abort_count"] = len(report["expected_lifecycle_aborts"])
        report["private_fixture_leak_count"] = len(report["privacy_leaks"])
        report["page_error_count"] = len(report["page_errors"])
        report["console_warning_or_error_count"] = len(report["console_messages"])
        report["status"] = "PASS"
        if (
            report["external_requests"]
            or report["bad_responses"]
            or report["failed_requests"]
            or report["page_errors"]
            or report["privacy_leaks"]
            or report["console_messages"]
        ):
            report["status"] = "FAIL"
            raise AssertionError(
                "E2E recorded external traffic, failed requests, page errors, or private-data leak"
            )
        if not report["owned_workers_joined"] or not report["identity_monitor_joined"]:
            report["status"] = "FAIL"
            raise AssertionError("owned dashboard workers did not join")

    manifest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    print(
        f"Browser E2E: {report['status']} | assertions={len(report['assertions'])} | "
        f"screenshots={len(screenshots)} | external={report['network_external_count']} | "
        f"expected_abort={report['expected_lifecycle_abort_count']} | "
        f"page_errors={report['page_error_count']} | "
        f"console_warn_error={report['console_warning_or_error_count']}"
    )
    print(f"Preview manifest: {manifest_path.resolve()} | SHA-256 {manifest_hash}")
    for entry in screenshots:
        if (
            entry.get("state") == "all synthetic sources opted-in; responsive dashboard/table"
            and entry.get("target_raster_px") == [1440, 900]
            and entry.get("css_zoom") == 1
        ):
            print(f"Representative PNG: {entry['path']} | SHA-256 {entry['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
