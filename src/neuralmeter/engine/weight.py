"""Peso progressivo por contexto acumulado (RF01.3). Funcoes puras, sem I/O.

PREMISSA (progress.md, Decisoes, 2026-11-10 "peso Claude"): unidade = 1 msg "curta";
acima de BASE chars o peso cresce linearmente; auto-calibracao (RF04.2) corrige a escala.
"""

from dataclasses import dataclass

CLAUDE_WEIGHT_BASE_CHARS = 8000
CLAUDE_WEIGHT_STEP_CHARS = 8000
CLAUDE_WEIGHT_MAX = 50.0


@dataclass(frozen=True)
class WeightParams:
    base_chars: float = CLAUDE_WEIGHT_BASE_CHARS
    step_chars: float = CLAUDE_WEIGHT_STEP_CHARS
    max_weight: float = CLAUDE_WEIGHT_MAX


DEFAULT_WEIGHT_PARAMS = WeightParams()


def compute_claude_weight(
    prompt_chars: int | None,
    context_chars: int | None,
    params: WeightParams = DEFAULT_WEIGHT_PARAMS,
) -> float:
    """Peso da mensagem Claude: 1.0 ate base_chars; depois 1 + (base-base_chars)/step_chars."""
    base = max(prompt_chars or 0, context_chars or 0, 0)
    if base <= params.base_chars:
        return 1.0
    weight = 1.0 + (base - params.base_chars) / params.step_chars
    return round(min(max(weight, 1.0), params.max_weight), 2)


def weight_for_usage(
    provider: str,
    prompt_chars: int | None,
    context_chars: int | None,
    params: WeightParams = DEFAULT_WEIGHT_PARAMS,
) -> float:
    """Claude -> peso por contexto; demais (ChatGPT: cota por mensagem) -> 1.0."""
    if provider == "claude":
        return compute_claude_weight(prompt_chars, context_chars, params)
    return 1.0
