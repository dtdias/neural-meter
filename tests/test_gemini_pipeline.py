import json
import os
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from neuralmeter.db import connect_db, insert_usage_event
from neuralmeter.server.ws_server import WsServer
from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.view_model import load_hud_view


def test_synthetic_gemini_hook_bridge_real_ws_server_db_and_hud(tmp_path, qapp):
    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("Node.js required for extension→server integration harness")
    db_path = tmp_path / "pre-existing-v5.sqlite3"
    initial = connect_db(db_path)
    now = datetime.now(UTC).replace(microsecond=0)
    insert_usage_event(
        initial,
        "claude",
        "claude-existing",
        prompt_chars=120,
        created_at=now - timedelta(minutes=1),
    )
    insert_usage_event(
        initial, "chatgpt", "gpt-existing", prompt_chars=80, created_at=now - timedelta(minutes=1)
    )
    # Simulate existing v4 DB to exercise actual v4→v5 migration during reopen.
    initial.execute("DROP TABLE quota_identity_observations")
    initial.execute("PRAGMA user_version=4")
    initial.commit()
    initial.close()

    # Reopen/migrate existing DB, preserving provider history and schema v5.
    conn = connect_db(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quota_identity_observations'"
        ).fetchone()
        is not None
    )
    updates = []
    update_event = threading.Event()
    ui_bridge = UpdateBridge()
    hud = HudWindow(view_loader=lambda instant: load_hud_view(conn, instant))
    hud.show()
    qapp.processEvents()
    assert "0 mensagens observadas" in hud.rows["gemini"].text_5h.text()
    ui_bridge.updated.connect(hud.on_update)

    def on_update(kind, payload):
        updates.append((kind, payload))
        if kind == "usage" and payload.get("provider") == "gemini":
            update_event.set()
        ui_bridge.emit_update(kind, payload)

    server = WsServer(conn, port=0, on_update=on_update)
    server.start()
    try:
        harness = (
            Path(__file__).resolve().parents[1]
            / "extension"
            / "test"
            / "gemini_pipeline_harness.mjs"
        )
        result = subprocess.run(
            [node, str(harness), str(server.port)],
            cwd=harness.parents[1],
            capture_output=True,
            text=True,
            timeout=12,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
            check=False,
        )
        assert result.returncode == 0, f"node stdout={result.stdout!r}; stderr={result.stderr!r}"
        assert result.stderr == ""
        output = json.loads(result.stdout)
        assert output["contains_prompt"] is False
        assert output["contains_token"] is False
        assert output["bridge_message"]["provider"] == "gemini"
        assert output["server_ack"]["type"] == "ack"
        assert output["server_ack"]["ref"] == "usage"
        assert update_event.wait(3), "backend did not signal Gemini usage update"

        rows = conn.execute(
            "SELECT provider, model_name, prompt_chars, context_chars FROM usage_events ORDER BY id"
        ).fetchall()
        counts = {
            row["provider"]: sum(1 for r in rows if r["provider"] == row["provider"])
            for row in rows
        }
        assert counts == {"claude": 1, "chatgpt": 1, "gemini": 1}
        gemini_row = next(row for row in rows if row["provider"] == "gemini")
        assert gemini_row["prompt_chars"] == output["bridge_message"]["prompt_chars"]
        assert gemini_row["model_name"] == "gemini-unknown"
        assert "synthetic integration prompt" not in repr(dict(gemini_row))

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            qapp.processEvents()
            if "1 mensagens observadas" in hud.rows["gemini"].text_5h.text():
                break
            time.sleep(0.01)
        assert any(
            kind == "usage" and payload.get("provider") == "gemini" for kind, payload in updates
        )
        assert "1 mensagens observadas" in hud.rows["gemini"].text_5h.text()
        view = load_hud_view(conn, datetime.now(UTC))["gemini"]
        assert view.quota_unknown is True
        assert view.messages_5h == view.messages_7d == 1
        assert view.reset_5h is None and view.reset_week is None
        row = hud.rows["gemini"]
        assert "desconhecida" in row.model_label.text()
        assert "1 mensagens" in row.text_5h.text()
        assert "quota desconhecida" in hud.compact_rows["gemini"].text.text()
    finally:
        server.stop()
        hud.close()
        conn.close()
