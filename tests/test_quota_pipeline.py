import json
from datetime import UTC, datetime, timedelta

from neuralmeter.db import (
    connect_db,
    get_quota_snapshots,
    observe_quota_identity,
    put_quota_snapshot,
)
from neuralmeter.server.handler import handle_message
from neuralmeter.server.protocol import parse_message
from neuralmeter.ui.view_model import load_hud_view


def _msg(account_hash, now, pct=0):
    return json.dumps(
        {
            "type": "quota",
            "provider": "claude",
            "account_hash": account_hash,
            "window": "5h",
            "percent": pct,
            "reset_at": int((now + timedelta(hours=1)).timestamp() * 1000),
            "observed_at": int(now.timestamp() * 1000),
        }
    )


def test_synthetic_protocol_db_view_flow_and_account_ambiguity(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    conn = connect_db(tmp_path / "quota.db")
    parsed = parse_message(_msg("a" * 64, now), now)
    observation = parse_message(
        json.dumps(
            {
                "type": "quota_observation",
                "provider": "claude",
                "account_hash": "a" * 64,
                "observed_at": int(now.timestamp() * 1000),
            }
        ),
        now,
    )
    handle_message(conn, observation)
    reply, update = handle_message(conn, parsed)
    assert reply["accepted"] is True and update[0] == "quota"
    views = load_hud_view(conn, now)
    assert views["claude"].pct_5h == 0
    assert views["claude"].source_5h == "oficial"
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 0
    assert "synthetic-org-A" not in repr(
        dict(conn.execute("SELECT * FROM quota_snapshots").fetchone())
    )

    second = now + timedelta(minutes=1)
    observed_b = parse_message(
        json.dumps(
            {
                "type": "quota_observation",
                "provider": "claude",
                "account_hash": "b" * 64,
                "observed_at": int(second.timestamp() * 1000),
            }
        ),
        second,
    )
    handle_message(conn, observed_b)  # malformed/empty response has identity, no quota metric
    assert get_quota_snapshots(conn, second)["status"] == "ambiguous"
    assert load_hud_view(conn, second)["claude"].source_5h == "estimado · contas ambíguas"
    conn.close()
    reopened = connect_db(tmp_path / "quota.db")
    assert get_quota_snapshots(reopened, second)["status"] == "ambiguous"
    assert load_hud_view(reopened, second)["claude"].source_5h == "estimado · contas ambíguas"
    reopened.close()


def test_new_identity_without_valid_metrics_never_keeps_old_account_official(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    conn = connect_db(tmp_path / "identity-only.db")
    observe_quota_identity(conn, "claude", "a" * 64, now)
    put_quota_snapshot(conn, "claude", "a" * 64, "5h", None, 42, now + timedelta(hours=1), now)
    assert get_quota_snapshots(conn, now)["status"] == "fresh"

    for account, minute in (("b" * 64, 1), ("c" * 64, 2), ("d" * 64, 3)):
        seen = now + timedelta(minutes=minute)
        handle_message(
            conn,
            parse_message(
                json.dumps(
                    {
                        "type": "quota_observation",
                        "provider": "claude",
                        "account_hash": account,
                        "observed_at": int(seen.timestamp() * 1000),
                    }
                ),
                seen,
            ),
        )
        result = get_quota_snapshots(conn, seen)
        assert result["status"] == "ambiguous"
        assert result["windows"] == {}
        assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 0
    # Once A observation ages out, B remains active but A's old metrics still cannot appear.
    later = now + timedelta(minutes=15)
    observe_quota_identity(conn, "claude", "b" * 64, later)
    assert get_quota_snapshots(conn, later)["status"] == "unavailable"
    assert get_quota_snapshots(conn, later)["windows"] == {}
    fallback = load_hud_view(conn, later)["claude"]
    assert fallback.identity_state == "confirmed"
    assert fallback.source_5h == "estimado · indisponível"
    conn.close()


def test_window_staleness_is_independent(tmp_path):
    now = datetime.now(UTC).replace(microsecond=0)
    conn = connect_db(tmp_path / "fresh-stale.db")
    account = "e" * 64
    observe_quota_identity(conn, "claude", account, now)
    put_quota_snapshot(conn, "claude", account, "5h", None, 30, now + timedelta(hours=1), now)
    put_quota_snapshot(
        conn,
        "claude",
        account,
        "weekly",
        None,
        80,
        now + timedelta(days=2),
        now - timedelta(minutes=11),
    )
    view = load_hud_view(conn, now)["claude"]
    assert view.source_5h == "oficial"
    assert view.source_week == "estimado · desatualizado"
    conn.close()
