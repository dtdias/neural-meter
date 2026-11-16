"""Bounded, opt-in reader for local Antigravity CLI transcript counters.

Only recognized JSONL row types and direct fields are inspected. Text remains in
memory for character-length estimates and is never returned or persisted.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as datetime_time
from pathlib import Path

from neuralmeter.model_catalog import (
    canonical_public_model_id,
    canonical_public_model_label,
)
from neuralmeter.model_catalog import (
    display_model_id as _display_public_model_id,
)

MAX_FILES = 256
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_VALIDATE_BYTES = 64 * 1024
MAX_ROWS = 10_000
MAX_LINE_BYTES = 128 * 1024
MAX_DEPTH = 32
MAX_STRING_CHARS = 64 * 1024
MAX_SCAN_SECONDS = 3.0
MAX_TOKEN_VALUE = 1_000_000_000_000
MAX_TOOL_CALLS_PER_ROW = 64
MAX_TEXT_ITEMS_PER_CONTAINER = 64
MAX_TEXT_NODES_PER_ROW = 4096
MAX_ROW_FIELDS = 256

_MODEL_SELECTION_PHRASE_RE = re.compile(
    re.escape("changed setting `Model Selection` from"), re.IGNORECASE
)
_MODEL_SETTING_RE = re.compile(
    r"<USER_SETTINGS_CHANGE>\s*.*?\bmodel\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9][A-Za-z0-9._:/+() -]{0,159}?)(?=[\"'}<,;\r\n]|$)",
    re.IGNORECASE | re.DOTALL,
)
MAX_MODEL_SETTINGS_BLOCKS = 32
MAX_MODEL_SETTINGS_CHARS = 4_096
MAX_MODEL_LABEL_CHARS = 160
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
_ROOT_ENV_KEYS = (
    "AGY_BRAIN_DIR",
    "ANTIGRAVITY_BRAIN_DIR",
    "AGY_HOME",
    "ANTIGRAVITY_HOME",
)


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _is_within(root: Path, path: Path) -> bool:
    try:
        root_norm = os.path.normcase(os.path.abspath(root))
        path_norm = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath((root_norm, path_norm)) == root_norm
    except (OSError, ValueError):
        return False


def _safe_absolute(path: Path) -> Path | None:
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        return None
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if _is_link_or_junction(current):
            return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


def _candidate_roots(roots: tuple[Path, ...] | None) -> list[Path]:
    if roots is not None:
        candidates = list(roots)
    else:
        candidates = []
        for key in _ROOT_ENV_KEYS[:2]:
            value = os.environ.get(key)
            if value:
                candidates.append(Path(value).expanduser())
        for key in _ROOT_ENV_KEYS[2:]:
            value = os.environ.get(key)
            if value:
                candidates.append(Path(value).expanduser() / "brain")
        home = Path.home()
        candidates.append(home / ".gemini" / "antigravity" / "brain")
    home_root = None
    if roots is None:
        try:
            home_root = Path.home().resolve(strict=True)
        except (OSError, RuntimeError):
            home_root = None
    found: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Path):
            continue
        root = _safe_absolute(candidate)
        if root is None:
            continue
        # Never treat a profile/ancestor such as `~`, `C:\Users`, or drive root
        # as a transcript root, including a misconfigured *_BRAIN_DIR.
        if home_root is not None and (
            os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(home_root))
            or _is_within(root, home_root)
        ):
            continue
        identity = os.path.normcase(str(root))
        if identity not in seen:
            found.append(root)
            seen.add(identity)
        if len(found) >= 8:
            break
    return found


def _transcript_paths(
    roots: list[Path], deadline: float, cancelled: threading.Event
) -> tuple[list[tuple[str, Path, Path]], bool]:
    paths: list[tuple[str, Path, Path]] = []
    partial = False
    seen_identity: set[tuple[int, int]] = set()
    for root_index, root in enumerate(roots):
        if cancelled.is_set() or time.monotonic() >= deadline:
            return paths, True
        try:
            with os.scandir(root) as conversations:
                for entry in conversations:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        return paths, True
                    conversation = root / entry.name
                    try:
                        if (
                            entry.is_symlink()
                            or _is_link_or_junction(conversation)
                            or not entry.is_dir(follow_symlinks=False)
                        ):
                            if entry.is_symlink() or _is_link_or_junction(conversation):
                                partial = True
                            continue
                        try:
                            with os.scandir(conversation) as nested_entries:
                                for nested in nested_entries:
                                    if cancelled.is_set() or time.monotonic() >= deadline:
                                        return paths, True
                                    nested_path = conversation / nested.name
                                    if nested.is_symlink() or _is_link_or_junction(nested_path):
                                        partial = True
                                    elif (
                                        nested.is_dir(follow_symlinks=False)
                                        and nested.name.lower() != ".system_generated"
                                    ):
                                        # Presence only; never recurse into nested transcript paths.
                                        partial = True
                        except OSError:
                            partial = True
                        # Only two documented transcript layouts; never recurse.
                        for canonical, candidate in (
                            (0, conversation / "transcript.jsonl"),
                            (
                                1,
                                conversation / ".system_generated" / "logs" / "transcript.jsonl",
                            ),
                        ):
                            if cancelled.is_set() or time.monotonic() >= deadline:
                                return paths, True
                            if not _safe_transcript(root, candidate):
                                continue
                            stat = candidate.stat(follow_symlinks=False)
                            if stat.st_size > MAX_FILE_BYTES:
                                partial = True
                                continue
                            identity = (stat.st_dev, stat.st_ino)
                            if identity in seen_identity:
                                continue
                            seen_identity.add(identity)
                            rel_depth = len(Path(os.path.relpath(candidate, root)).parts)
                            rank = f"{canonical}:{rel_depth:04d}"
                            key = f"{root_index}:{conversation.name}:{rank}"
                            paths.append((key, root, candidate))
                            if len(paths) >= MAX_FILES * 2:
                                return paths, True
                    except (OSError, RuntimeError, ValueError):
                        partial = True
        except OSError:
            partial = True
    return paths, partial


def _safe_transcript(root: Path, path: Path) -> bool:
    """Containment and no-follow checks; path must be one known layout under root."""
    try:
        current_root = Path(root.anchor)
        for part in root.parts[1:]:
            current_root = current_root / part
            if _is_link_or_junction(current_root):
                return False
        root_real = root.resolve(strict=True)
        if path.is_symlink() or _is_link_or_junction(path):
            return False
        if not _is_within(root_real, path):
            return False
        current = root_real
        relative_parts = Path(os.path.relpath(path, root_real)).parts
        for part in relative_parts[:-1]:
            current = current / part
            if _is_link_or_junction(current):
                return False
        resolved = path.resolve(strict=True)
        if not _is_within(root_real, resolved):
            return False
        stat = path.stat(follow_symlinks=False)
        return path.is_file() and stat.st_size >= 0
    except (OSError, RuntimeError, ValueError):
        return False


@contextmanager
def _open_guarded(
    root: Path,
    path: Path,
    cancelled: threading.Event,
) -> Iterator[object | None]:
    """Open a known file only after containment checks; verify identity before reading."""
    stream = None
    try:
        if cancelled.is_set() or not _safe_transcript(root, path):
            yield None
            return
        stream = path.open("rb")
        opened = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            stream.close()
            yield None
            return
        if cancelled.is_set() or not _safe_transcript(root, path):
            stream.close()
            yield None
            return
    except (OSError, RuntimeError, ValueError):
        if stream is not None:
            stream.close()
        yield None
        return
    try:
        yield stream
    finally:
        stream.close()


def _valid_counter(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > MAX_TOKEN_VALUE:
        return None
    return value


def _bounded_model(value) -> str | None:
    return canonical_public_model_id(value)


def _bounded_selected_model_label(value: object) -> str | None:
    return canonical_public_model_label(value)


def _selected_model_from_content(content: object) -> tuple[bool, str | None]:
    """Bounded Token Meter AGY selection-text projection; result remains allowlisted."""
    if not isinstance(content, str):
        return False, None
    text = content[:MAX_STRING_CHARS]
    setting = _MODEL_SETTING_RE.search(text)
    if setting:
        return True, _bounded_selected_model_label(setting.group(1).strip())
    found_selection = False
    start = 0
    for _ in range(MAX_MODEL_SETTINGS_BLOCKS):
        opening = text.find("<USER_SETTINGS_CHANGE>", start)
        if opening < 0:
            break
        body_start = opening + len("<USER_SETTINGS_CHANGE>")
        close = text.find(
            "</USER_SETTINGS_CHANGE>",
            body_start,
            body_start + MAX_MODEL_SETTINGS_CHARS + len("</USER_SETTINGS_CHANGE>"),
        )
        if close < 0:
            start = body_start
            continue
        block = text[body_start:close]
        phrase = _MODEL_SELECTION_PHRASE_RE.search(block)
        if phrase:
            found_selection = True
            line_end = block.find("\n", phrase.end())
            stop = len(block) if line_end < 0 else line_end
            for delimiter in ("`", "<"):
                position = block.find(delimiter, phrase.end(), stop)
                if position >= 0:
                    stop = min(stop, position)
            words = block[phrase.end() : stop].split()
            lowered = [word.casefold() for word in words]
            if "to" in lowered:
                label = " ".join(words[lowered.index("to") + 1 :]).rstrip("`.,; ")
                if len(label) <= MAX_MODEL_LABEL_CHARS:
                    return True, _bounded_selected_model_label(label)
            return True, None
        start = close + len("</USER_SETTINGS_CHANGE>")
    return found_selection, None


def display_model_id(value) -> str:
    """Return only a verified public model ID, never arbitrary transcript text."""
    return _display_public_model_id(value)


def _timestamp(row: dict, *, now_utc: datetime) -> datetime | None:
    value = row.get("timestamp")
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.year < 2000 or parsed.year > 2100:
        return None
    parsed = parsed.astimezone(UTC)
    if parsed > now_utc:
        return None
    return parsed


def _period_bounds(
    now: datetime | None,
    timezone: tzinfo | None,
) -> tuple[datetime, datetime, datetime, str, str, str]:
    """UTC boundaries for local calendar dates, using Qt's Windows DST zone by default."""
    if now is not None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        zone = timezone or now.tzinfo
        local_now = now.astimezone(zone)
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        seven_day = datetime.combine(today.date() - timedelta(days=6), datetime_time.min, zone)
        return (
            now.astimezone(UTC),
            today.astimezone(UTC),
            seven_day.astimezone(UTC),
            today.strftime("%Y-%m-%d %Z"),
            seven_day.strftime("%Y-%m-%d %Z"),
            local_now.strftime("%Y-%m-%d %H:%M %Z"),
        )

    from PySide6.QtCore import QDateTime, QTime, QTimeZone

    zone = QTimeZone.systemTimeZone()
    local_now_qt = QDateTime.currentDateTime().toTimeZone(zone)
    local_date = local_now_qt.date()
    today_qt = QDateTime(local_date, QTime(0, 0), zone)
    seven_qt = QDateTime(local_date.addDays(-6), QTime(0, 0), zone)
    return (
        local_now_qt.toUTC().toPython().replace(tzinfo=UTC),
        today_qt.toUTC().toPython().replace(tzinfo=UTC),
        seven_qt.toUTC().toPython().replace(tzinfo=UTC),
        today_qt.toString("yyyy-MM-dd t"),
        seven_qt.toString("yyyy-MM-dd t"),
        local_now_qt.toString("yyyy-MM-dd HH:mm t"),
    )


def _add_period_row(
    target: dict,
    row: dict,
    measured: dict[str, int],
    input_chars: int,
    output_chars: int,
    coverage_partial: bool,
) -> None:
    target["rows"] += 1
    target["coverage_partial"] = target["coverage_partial"] or coverage_partial
    for key, value in measured.items():
        target[key] = (target[key] or 0) + value
        target[f"{key}_rows"] += 1
    if measured:
        target["reported_rows"] += 1
    if row.get("type") == "USER_INPUT" and input_chars:
        target["calculated_input_tokens"] += math.ceil(input_chars / 4)
        target["calculated_input_rows"] += 1
        target["calculated_rows"] += 1
    if row.get("type") == "PLANNER_RESPONSE" and "output_tokens" not in row and output_chars:
        target["calculated_output_tokens"] += math.ceil(output_chars / 4)
        target["calculated_output_rows"] += 1
        target["calculated_rows"] += 1


def _has_recognized_row(
    root: Path,
    path: Path,
    deadline: float,
    cancelled: threading.Event,
) -> bool:
    """Validate canonical layout cheaply; malformed/nonempty file is not sufficient."""
    try:
        with _open_guarded(root, path, cancelled) as stream:
            if stream is None:
                return False
            bytes_read = 0
            for _ in range(8):
                if cancelled.is_set() or time.monotonic() >= deadline:
                    return False
                remaining = MAX_VALIDATE_BYTES - bytes_read
                if remaining <= 0:
                    return False
                raw = stream.readline(min(MAX_LINE_BYTES + 1, remaining + 1))
                if cancelled.is_set() or not raw:
                    return False
                bytes_read += len(raw)
                if len(raw) > MAX_LINE_BYTES or bytes_read > MAX_VALIDATE_BYTES:
                    continue
                try:
                    row = json.loads(raw)
                except (ValueError, TypeError, RecursionError):
                    continue
                if isinstance(row, dict) and row.get("type") in {
                    "USER_INPUT",
                    "PLANNER_RESPONSE",
                }:
                    return True
    except (OSError, ValueError):
        return False
    return False


def _text_chars(
    value,
    depth: int = 0,
    budget: list[int] | None = None,
) -> tuple[int, bool]:
    if budget is None:
        budget = [MAX_TEXT_NODES_PER_ROW]
    if depth > MAX_DEPTH or budget[0] <= 0:
        return 0, True
    budget[0] -= 1
    if isinstance(value, str):
        return min(len(value), MAX_STRING_CHARS), len(value) > MAX_STRING_CHARS
    if isinstance(value, list | dict):
        items = list(value.values()) if isinstance(value, dict) else value
        total = 0
        partial = len(items) > MAX_TEXT_ITEMS_PER_CONTAINER
        for item in items[:MAX_TEXT_ITEMS_PER_CONTAINER]:
            amount, item_partial = _text_chars(item, depth + 1, budget)
            total += amount
            partial |= item_partial
            if budget[0] <= 0:
                partial = True
                break
        return total, partial
    return 0, False


def _row_text(
    row: dict,
    row_type: str,
    *,
    need_input: bool = True,
    need_output: bool = True,
) -> tuple[int, int, bool]:
    """Return bounded character estimates and flag any omitted text coverage."""
    budget = [MAX_TEXT_NODES_PER_ROW]
    partial = False
    if row_type == "USER_INPUT":
        if not need_input:
            return 0, 0, False
        input_chars, partial = _text_chars(row.get("content"), budget=budget)
        return input_chars, 0, partial
    if row_type != "PLANNER_RESPONSE":
        return 0, 0, False
    output = 0
    if not need_output:
        return 0, 0, False
    for key in ("content", "text", "thinking"):
        if key in row:
            amount, item_partial = _text_chars(row[key], budget=budget)
            output += amount
            partial |= item_partial
    calls = row.get("tool_calls")
    if isinstance(calls, str):
        amount, item_partial = _text_chars(calls, budget=budget)
        output += amount
        partial |= item_partial
    elif isinstance(calls, list):
        if len(calls) > MAX_TOOL_CALLS_PER_ROW:
            partial = True
        for call in calls[:MAX_TOOL_CALLS_PER_ROW]:
            if not isinstance(call, dict):
                continue
            if "args" in call:
                amount, item_partial = _text_chars(call["args"], budget=budget)
            elif "arguments" in call:
                amount, item_partial = _text_chars(call["arguments"], budget=budget)
            else:
                continue
            output += amount
            partial |= item_partial
            if budget[0] <= 0:
                partial = True
                break
    return 0, output, partial


def _new_model() -> dict:
    result = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "calculated_input_tokens": 0,
        "calculated_output_tokens": 0,
        "calculated_input_rows": 0,
        "calculated_output_rows": 0,
        "reported_rows": 0,
        "calculated_rows": 0,
        "rows": 0,
        "unknown_timestamp_rows": 0,
        "coverage_partial": False,
    }
    result.update({f"{field}_rows": 0 for field in _TOKEN_FIELDS})
    return result


def _merge_model_metrics(target: dict, source: dict) -> None:
    for field, value in source.items():
        if value is None:
            continue
        if field == "coverage_partial":
            target[field] = target[field] or bool(value)
            continue
        if field in _TOKEN_FIELDS:
            target[field] = (target[field] or 0) + value
        else:
            target[field] += value


def _read_one(
    root: Path,
    path: Path,
    deadline: float,
    cancelled: threading.Event,
    budget_left: int,
    row_budget: int,
    now_utc: datetime,
    today_start: datetime,
    seven_day_start: datetime,
):
    result: dict[str, dict] = defaultdict(_new_model)
    period_results = {
        "today": defaultdict(_new_model),
        "last_7_days": defaultdict(_new_model),
    }
    partial = False
    rows = 0
    bytes_read = 0
    period: list[datetime] = []
    unknown_timestamp_rows = 0
    try:
        if not _safe_transcript(root, path):
            return result, period_results, True, 0, 0, period, unknown_timestamp_rows
        stat = path.stat(follow_symlinks=False)
        if not path.is_file() or stat.st_size > MAX_FILE_BYTES or stat.st_size > budget_left:
            return result, period_results, True, 0, 0, period, unknown_timestamp_rows
        with _open_guarded(root, path, cancelled) as stream:
            if stream is None:
                return result, period_results, True, 0, 0, period, unknown_timestamp_rows
            while rows < row_budget and bytes_read < min(MAX_FILE_BYTES, budget_left):
                if cancelled.is_set() or time.monotonic() >= deadline:
                    partial = True
                    break
                raw = stream.readline(min(MAX_LINE_BYTES + 1, budget_left - bytes_read + 1))
                if not raw:
                    break
                rows += 1
                bytes_read += len(raw)
                if len(raw) > MAX_LINE_BYTES or bytes_read > budget_left:
                    partial = True
                    break
                try:
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        partial = True
                        continue
                    row_partial = len(row) > MAX_ROW_FIELDS
                    partial |= row_partial
                    row_type = row.get("type")
                    if row_type not in {"USER_INPUT", "PLANNER_RESPONSE"}:
                        partial = True
                        continue
                    model = _bounded_model(row.get("model"))
                    if model is None:
                        model = _bounded_model(row.get("modelId"))
                    if model is None:
                        model = _bounded_model(row.get("provider"))
                    content = row.get("content")
                    if row_type == "USER_INPUT" and isinstance(content, str):
                        changed, selected = _selected_model_from_content(content)
                        if changed and model is None:
                            model = selected or "Modelo não identificado"
                        if len(content) > MAX_STRING_CHARS:
                            row_partial = True
                    model = model or "Modelo não identificado"
                    metrics = result[model]
                    metrics["rows"] += 1
                    stamp = _timestamp(row, now_utc=now_utc)
                    if stamp is not None:
                        period.append(stamp)
                    else:
                        metrics["unknown_timestamp_rows"] += 1
                        unknown_timestamp_rows += 1
                    measured: dict[str, int] = {}
                    if row_type == "PLANNER_RESPONSE":
                        for field in _TOKEN_FIELDS:
                            if field in row:
                                value = _valid_counter(row[field])
                                if value is None:
                                    row_partial = True
                                else:
                                    measured[field] = value
                                    metrics[field] = (metrics[field] or 0) + value
                                    metrics[f"{field}_rows"] += 1
                    input_chars, output_chars, text_partial = _row_text(
                        row,
                        row_type,
                        need_input=row_type == "USER_INPUT" and "input_tokens" not in row,
                        need_output=row_type == "PLANNER_RESPONSE" and "output_tokens" not in row,
                    )
                    row_partial |= text_partial
                    partial |= row_partial
                    metrics["coverage_partial"] |= row_partial
                    if measured:
                        metrics["reported_rows"] += 1
                    if "input_tokens" not in row and input_chars:
                        metrics["calculated_input_tokens"] += math.ceil(input_chars / 4)
                        metrics["calculated_input_rows"] += 1
                        metrics["calculated_rows"] += 1
                    if "output_tokens" not in row and output_chars:
                        metrics["calculated_output_tokens"] += math.ceil(output_chars / 4)
                        metrics["calculated_output_rows"] += 1
                        metrics["calculated_rows"] += 1
                    if stamp is not None and today_start <= stamp <= now_utc:
                        _add_period_row(
                            period_results["today"][model],
                            row,
                            measured,
                            input_chars,
                            output_chars,
                            row_partial,
                        )
                    if stamp is not None and seven_day_start <= stamp <= now_utc:
                        _add_period_row(
                            period_results["last_7_days"][model],
                            row,
                            measured,
                            input_chars,
                            output_chars,
                            row_partial,
                        )
                except (ValueError, TypeError, RecursionError, UnicodeDecodeError):
                    partial = True
            if rows >= row_budget and stream.read(1):
                partial = True
            if bytes_read >= min(MAX_FILE_BYTES, budget_left) and stream.read(1):
                partial = True
    except (OSError, ValueError, RecursionError):
        partial = True
    return (
        result,
        period_results,
        partial,
        rows,
        bytes_read,
        period,
        unknown_timestamp_rows,
    )


def read_antigravity_usage(
    *,
    consent_granted: bool = False,
    roots: tuple[Path, ...] | None = None,
    cancel_event: threading.Event | None = None,
    max_seconds: float = MAX_SCAN_SECONDS,
    now: datetime | None = None,
    timezone: tzinfo | None = None,
) -> dict:
    """Read bounded local history; caller must gate every call on separate opt-in."""
    cancelled = cancel_event or threading.Event()
    deadline = time.monotonic() + min(max(float(max_seconds), 0.01), MAX_SCAN_SECONDS)
    if cancelled.is_set():
        return _empty("cancelled")
    if consent_granted is not True:
        return _empty("consent_required")
    (
        now_utc,
        today_start,
        seven_start,
        today_label,
        seven_label,
        now_label,
    ) = _period_bounds(now, timezone)
    safe_roots = _candidate_roots(roots)
    if not safe_roots:
        return _empty("unavailable")
    candidates, partial = _transcript_paths(safe_roots, deadline, cancelled)
    if cancelled.is_set():
        return _empty("cancelled")
    # Current and legacy transcript layouts represent one conversation. Prefer
    # one readable nonempty canonical file; never sum both layouts together.
    grouped: dict[str, list[tuple[int, int, Path, Path]]] = defaultdict(list)
    for key, root, path in candidates:
        bucket, canonical, depth = key.rsplit(":", 2)
        grouped[bucket].append((int(canonical), int(depth), root, path))
    chosen: list[tuple[Path, Path]] = []
    for options in grouped.values():
        if cancelled.is_set():
            return _empty("cancelled")
        options.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        if len(options) > 2 or sum(item[0] for item in options) > 1:
            partial = True
        selected = None
        for canonical, _depth, root, path in options:
            try:
                size = path.stat().st_size
                if 0 < size <= MAX_FILE_BYTES and _has_recognized_row(
                    root, path, deadline, cancelled
                ):
                    selected = (root, path)
                    break
                if canonical:
                    partial = True
            except OSError:
                partial = True
        if selected:
            chosen.append(selected)
        else:
            partial = True
    if cancelled.is_set():
        return _empty("cancelled")
    if len(chosen) > MAX_FILES:
        chosen = chosen[:MAX_FILES]
        partial = True

    totals: dict[str, dict] = defaultdict(_new_model)
    period_totals = {
        "today": defaultdict(_new_model),
        "last_7_days": defaultdict(_new_model),
    }
    seen_files: set[tuple[int, int]] = set()
    period: list[datetime] = []
    total_rows = total_bytes = covered_files = 0
    unknown_timestamp_rows = 0
    for root, path in chosen:
        if cancelled.is_set() or time.monotonic() >= deadline or total_rows >= MAX_ROWS:
            partial = True
            break
        try:
            stat = path.stat(follow_symlinks=False)
            file_id = (stat.st_dev, stat.st_ino)
            if file_id in seen_files:
                continue
            seen_files.add(file_id)
        except OSError:
            partial = True
            continue
        if total_bytes >= MAX_TOTAL_BYTES:
            partial = True
            break
        (
            parsed,
            file_periods,
            file_partial,
            rows,
            amount,
            stamps,
            file_unknown_dates,
        ) = _read_one(
            root,
            path,
            deadline,
            cancelled,
            MAX_TOTAL_BYTES - total_bytes,
            MAX_ROWS - total_rows,
            now_utc,
            today_start,
            seven_start,
        )
        partial |= file_partial
        total_rows += rows
        total_bytes += amount
        period.extend(stamps)
        unknown_timestamp_rows += file_unknown_dates
        if rows and not file_partial:
            covered_files += 1
        for model, values in parsed.items():
            target = totals[model]
            for field, value in values.items():
                if value is None:
                    continue
                if field in _TOKEN_FIELDS:
                    target[field] = (target[field] or 0) + value
                elif field == "coverage_partial":
                    target[field] = target[field] or bool(value)
                else:
                    target[field] += value
        for period_name, models_for_period in file_periods.items():
            for model, values in models_for_period.items():
                target = period_totals[period_name][model]
                _merge_model_metrics(target, values)
    models = [
        {"model": model, **values}
        for model, values in sorted(totals.items(), key=lambda pair: pair[0].casefold())
    ]
    temporal_partial = partial or unknown_timestamp_rows > 0
    period_payload = {}
    for period_name, source in period_totals.items():
        period_models = [
            {"model": model, **values}
            for model, values in sorted(source.items(), key=lambda pair: pair[0].casefold())
        ]
        period_payload[period_name] = {
            "models": period_models,
            "partial": temporal_partial,
            "coverage_partial": temporal_partial,
            "unknown_timestamp_rows": unknown_timestamp_rows,
            "boundary_start_local": today_label if period_name == "today" else seven_label,
            "boundary_end_local": now_label,
        }
    period_payload["history"] = {
        "models": models,
        "partial": temporal_partial,
        "coverage_partial": temporal_partial,
        "unknown_timestamp_rows": unknown_timestamp_rows,
        "observed_start": min(period).isoformat() if period else None,
        "observed_end": max(period).isoformat() if period else None,
        "range_is_complete": False,
    }
    return {
        "status": "partial" if partial else "available" if models else "no_local_data",
        "models": models,
        "coverage_partial": temporal_partial,
        "files_read": covered_files,
        "rows_read": total_rows,
        "period_start": min(period).isoformat() if period else None,
        "period_end": max(period).isoformat() if period else None,
        "periods": period_payload,
        "temporal_coverage": {
            "partial": temporal_partial,
            "coverage_partial": temporal_partial,
            "unknown_timestamp_rows": unknown_timestamp_rows,
            "range_is_complete": False,
        },
        "calculation": "chars_div_4_ceil",
        "semantics_note": (
            "Contagens por linha observada; sem garantia de delta/cumulativo ou total da conta."
        ),
    }


def _empty(status: str) -> dict:
    return {
        "status": status,
        "models": [],
        "coverage_partial": status == "partial",
        "files_read": 0,
        "rows_read": 0,
        "period_start": None,
        "period_end": None,
        "calculation": "chars_div_4_ceil",
        "semantics_note": "Contagens locais não equivalem a quota nem faturamento.",
        "temporal_coverage": {
            "partial": status == "partial",
            "coverage_partial": status == "partial",
        },
    }


def _conversation_dir(path: Path) -> Path:
    if (
        path.parent.name.lower() == "logs"
        and path.parent.parent.name.lower() == ".system_generated"
    ):
        return path.parent.parent.parent
    return path.parent
