from datetime import UTC, datetime, timedelta

from neuralmeter.ui.renewal import RenewalTracker, renewal_eligible, renewal_message
from neuralmeter.ui.view_model import ProviderView, level_for_percent

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _view(provider="claude", pct=50.0, reset=None, blocked=False):
    return ProviderView(
        provider=provider,
        label=provider.title(),
        pct_5h=pct,
        level_5h=level_for_percent(pct, blocked),
        reset_5h=reset,
        pct_week=0.0,
        level_week="green",
        reset_week=None,
        last_model="m",
        rate_limited=blocked,
    )


def test_claude_due_once():
    t = RenewalTracker()
    t.observe({"claude": _view(pct=30, reset=NOW + timedelta(minutes=10))})
    assert t.due(NOW) == []
    later = NOW + timedelta(minutes=11)
    assert t.due(later) == ["claude"]
    assert t.due(later) == []


def test_future_reset_not_due():
    t = RenewalTracker()
    t.observe({"claude": _view(pct=30, reset=NOW + timedelta(hours=1))})
    assert t.due(NOW + timedelta(minutes=59)) == []


def test_claude_zero_never_fires():
    t = RenewalTracker()
    t.observe({"claude": _view(pct=0, reset=NOW + timedelta(minutes=1))})
    assert t.due(NOW + timedelta(hours=1)) == []


def test_chatgpt_thresholds():
    reset = NOW + timedelta(minutes=1)
    later = NOW + timedelta(hours=1)
    t = RenewalTracker()
    t.observe({"chatgpt": _view("chatgpt", pct=50, reset=reset)})
    assert t.due(later) == []
    t.observe({"chatgpt": _view("chatgpt", pct=92, reset=reset)})
    assert t.due(later) == ["chatgpt"]
    t2 = RenewalTracker()
    t2.observe({"chatgpt": _view("chatgpt", pct=10, reset=reset, blocked=True)})
    assert t2.due(later) == ["chatgpt"]


def test_reset_none_keeps_memory():
    t = RenewalTracker()
    t.observe({"claude": _view(pct=40, reset=NOW + timedelta(minutes=5))})
    t.observe({"claude": _view(pct=0, reset=None)})
    assert t.due(NOW + timedelta(minutes=6)) == ["claude"]


def test_new_reset_fires_again():
    t = RenewalTracker()
    r1 = NOW + timedelta(minutes=5)
    t.observe({"claude": _view(pct=40, reset=r1)})
    assert t.due(r1 + timedelta(seconds=1)) == ["claude"]
    r2 = r1 + timedelta(hours=5)
    t.observe({"claude": _view(pct=20, reset=r2)})
    assert t.due(r1 + timedelta(hours=1)) == []
    assert t.due(r2 + timedelta(seconds=1)) == ["claude"]


def test_stable_order():
    t = RenewalTracker()
    r = NOW + timedelta(minutes=1)
    t.observe({"chatgpt": _view("chatgpt", pct=95, reset=r), "claude": _view(pct=10, reset=r)})
    assert t.due(NOW + timedelta(hours=1)) == ["claude", "chatgpt"]


def test_message_and_eligible_other():
    title, body = renewal_message("claude")
    assert title == "NeuralMeter" and "Claude" in body
    assert not renewal_eligible(_view("outro", pct=99))
