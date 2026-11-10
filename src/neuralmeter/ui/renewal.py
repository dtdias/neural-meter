"""Deteccao de renovacao da cota 5h (puro, sem Qt)."""

from datetime import datetime

from neuralmeter.ui.view_model import PROVIDER_LABELS, ProviderView

PROVIDER_ORDER = ("claude", "chatgpt")
CHATGPT_NOTIFY_PCT = 90.0


def renewal_eligible(view: ProviderView) -> bool:
    """Vale avisar a renovacao? Evita spam quando nao houve uso relevante."""
    if view.provider == "claude":
        return view.pct_5h > 0 or view.rate_limited
    if view.provider == "chatgpt":
        return view.rate_limited or view.pct_5h >= CHATGPT_NOTIFY_PCT
    return False


def renewal_message(provider: str) -> tuple[str, str]:
    label = PROVIDER_LABELS.get(provider, provider)
    return ("NeuralMeter", f"Cota 5h do {label} renovada")


class RenewalTracker:
    """Memoriza o ultimo reset_5h visto por provedor e dispara 1x quando ele passa."""

    def __init__(self) -> None:
        self._state: dict[str, tuple[datetime, bool]] = {}
        self._notified: set[tuple[str, str]] = set()

    def observe(self, views: dict[str, ProviderView]) -> None:
        for provider, view in views.items():
            # reset None (snapshot pos-reset) nao apaga a memoria.
            if view.reset_5h is not None:
                self._state[provider] = (view.reset_5h, renewal_eligible(view))

    def due(self, now: datetime) -> list[str]:
        out: list[str] = []
        providers = sorted(
            self._state,
            key=lambda p: (
                PROVIDER_ORDER.index(p) if p in PROVIDER_ORDER else len(PROVIDER_ORDER),
                p,
            ),
        )
        for provider in providers:
            reset, eligible = self._state[provider]
            if reset > now:
                continue
            del self._state[provider]
            key = (provider, reset.isoformat())
            if eligible and key not in self._notified:
                self._notified.add(key)
                out.append(provider)
        return out
