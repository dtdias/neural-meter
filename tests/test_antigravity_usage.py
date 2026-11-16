import io
import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

from neuralmeter.antigravity_usage import (
    MAX_FILE_BYTES,
    MAX_TOOL_CALLS_PER_ROW,
    _selected_model_from_content,
)
from neuralmeter.antigravity_usage import read_antigravity_usage as _read_without_consent
from neuralmeter.model_catalog import canonical_public_model_id, display_model_id


def read_antigravity_usage(**kwargs):
    return _read_without_consent(consent_granted=True, **kwargs)


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def _model(result, name):
    return next(item for item in result["models"] if item["model"] == name)


class _Pacific2026(tzinfo):
    def utcoffset(self, value):
        if value is None:
            return timedelta(hours=-8)
        daylight_start = (value.month, value.day, value.hour) >= (3, 8, 3)
        daylight_end = (value.month, value.day, value.hour) >= (11, 1, 1)
        return timedelta(hours=-7 if daylight_start and not daylight_end else -8)

    def dst(self, value):
        return self.utcoffset(value) - timedelta(hours=-8)

    def tzname(self, value):
        return "PDT" if self.dst(value) else "PST"

    def fromutc(self, value):
        start = datetime(2026, 3, 8, 10, tzinfo=self)
        end = datetime(2026, 11, 1, 9, tzinfo=self)
        offset = timedelta(hours=-7) if start <= value < end else timedelta(hours=-8)
        return (value + offset).replace(tzinfo=self)


def test_reads_only_exact_planner_response_direct_counters_and_preserves_zero(tmp_path):
    transcript = _write(
        tmp_path / "session-a" / ".system_generated" / "logs" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": "GEMINI-2.5-FLASH",
                "input_tokens": 0,
                "output_tokens": 9,
                "timestamp": "2026-11-15T23:30:00Z",
            },
            {"type": "planner_step", "usage": {"input_tokens": 500}},
        ],
    )

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "partial"
    model = _model(result, "gemini-2.5-flash")
    assert model["input_tokens"] == 0
    assert model["output_tokens"] == 9
    assert model["cache_read_tokens"] is None
    assert model["cache_write_tokens"] is None
    assert result["period_start"] == "2026-11-15T23:30:00+00:00"
    assert "usage" not in json.dumps(result)
    assert str(transcript) not in json.dumps(result)


def test_invalid_counter_values_never_replace_with_zero_or_fabricate(tmp_path):
    _write(
        tmp_path / "s" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": "gemini-2.5-pro",
                "input_tokens": True,
                "output_tokens": "12",
                "cache_read_tokens": -1,
                "cache_write_tokens": 1.5,
            }
        ],
    )

    result = read_antigravity_usage(roots=(tmp_path,))
    model = _model(result, "gemini-2.5-pro")

    assert result["status"] == "partial"
    assert model["input_tokens"] is None
    assert model["output_tokens"] is None
    assert model["cache_read_tokens"] is None
    assert model["cache_write_tokens"] is None
    assert model["coverage_partial"] is True


def test_character_fallback_is_approximate_separate_and_never_returns_content(tmp_path):
    secret = "private prompt sentinel"
    _write(
        tmp_path / "s" / "transcript.jsonl",
        [
            {"type": "USER_INPUT", "model": "gemini-2.5-pro", "content": secret},
            {
                "type": "PLANNER_RESPONSE",
                "model": "gemini-2.5-pro",
                "thinking": "hidden reasoning sentinel",
                "tool_calls": [{"args": {"query": "tool input sentinel"}}],
            },
        ],
    )

    result = read_antigravity_usage(roots=(tmp_path,))
    model = _model(result, "gemini-2.5-pro")

    assert model["input_tokens"] is None
    assert model["output_tokens"] is None
    assert model["calculated_input_tokens"] == (len(secret) + 3) // 4
    output_chars = len("hidden reasoning sentinel") + len("tool input sentinel")
    assert model["calculated_output_tokens"] == (output_chars + 3) // 4
    assert result["calculation"] == "chars_div_4_ceil"
    serialized = json.dumps(result)
    assert secret not in serialized
    assert "hidden reasoning sentinel" not in serialized
    assert "tool input sentinel" not in serialized


def test_model_catalog_casefold_only_and_never_echoes_unknown_private_value():
    assert canonical_public_model_id("GEMINI-3.1-FLASH-LITE") == "gemini-3.1-flash-lite"
    assert canonical_public_model_id("claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert canonical_public_model_id("gpt-6-luna") == "gpt-6-luna"
    assert canonical_public_model_id(" gemini-2.5-pro") is None
    secret = "private-user-model@example.invalid"
    assert display_model_id(secret) == "Modelo não identificado"
    assert secret not in display_model_id(secret)


def test_token_meter_selection_projection_only_returns_allowlisted_model():
    selected = (
        "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
        "gemini-2.5-pro to gemini-2.5-flash.</USER_SETTINGS_CHANGE>"
    )
    secret = (
        "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
        "previous to private-user-model@example.invalid.</USER_SETTINGS_CHANGE>"
    )
    assert _selected_model_from_content(selected) == (True, "gemini-2.5-flash")
    assert _selected_model_from_content(secret) == (True, None)
    assert _selected_model_from_content("ordinary prompt text") == (False, None)
    assert "private-user-model" not in repr(_selected_model_from_content(secret))


def test_actual_user_settings_labels_resolve_only_to_allowlisted_ids():
    cases = (
        (
            "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
            "Gemini 2.5 Pro to Gemini 3.8 Flash (High)."
            "</USER_SETTINGS_CHANGE>",
            (True, "gemini-3.8-flash"),
        ),
        (
            "<USER_SETTINGS_CHANGE>{model: Gemini 2.5 Pro}</USER_SETTINGS_CHANGE>",
            (True, "gemini-2.5-pro"),
        ),
        (
            "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
            "Gemini 3.6 Flash to Claude Sonnet 4.5."
            "</USER_SETTINGS_CHANGE>",
            (True, "claude-sonnet-4-5"),
        ),
    )
    for content, expected in cases:
        assert _selected_model_from_content(content) == expected

    private = (
        "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
        "Gemini 2.5 Pro to Private Quarterly Plan."
        "</USER_SETTINGS_CHANGE>"
    )
    unknown_qualifier = (
        "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
        "Gemini 2.5 Pro to Gemini 2.5 Pro (Private Quarterly Plan)."
        "</USER_SETTINGS_CHANGE>"
    )
    assert _selected_model_from_content(private) == (True, None)
    assert _selected_model_from_content(unknown_qualifier) == (True, None)
    assert "Private Quarterly Plan" not in repr(_selected_model_from_content(private))
    assert "Private Quarterly Plan" not in repr(_selected_model_from_content(unknown_qualifier))


def test_user_settings_selection_human_labels_count_without_echoing_unknowns(tmp_path):
    private = "private-user-model@example.invalid"
    _write(
        tmp_path / "brain" / "session" / "transcript.jsonl",
        [
            {
                "type": "USER_INPUT",
                "timestamp": datetime.now(UTC).isoformat(),
                "content": (
                    "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
                    "Gemini 2.5 Pro to Gemini 3.8 Flash (High)."
                    "</USER_SETTINGS_CHANGE>"
                ),
            },
            {
                "type": "USER_INPUT",
                "timestamp": datetime.now(UTC).isoformat(),
                "content": (
                    "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` from "
                    f"Gemini 3.8 Flash (High) to {private}."
                    "</USER_SETTINGS_CHANGE>"
                ),
            },
        ],
    )
    result = read_antigravity_usage(roots=(tmp_path / "brain",))
    serialized = json.dumps(result)
    labels = {item["model"] for item in result["models"]}
    assert "gemini-3.8-flash" in labels
    assert "Modelo não identificado" in labels
    assert private not in serialized
    assert "Private Quarterly Plan" not in serialized


def test_token_meter_selected_gemini_id_projects_to_sanitized_transcript_count(tmp_path):
    secret = "sentinel transcript payload; never expose"
    _write(
        tmp_path / "brain" / "conversation" / "transcript.jsonl",
        [
            {
                "type": "USER_INPUT",
                "timestamp": datetime.now(UTC).isoformat(),
                "content": (
                    "<USER_SETTINGS_CHANGE>Changed setting `Model Selection` "
                    "from gemini-2.5-pro to gemini-2.5-flash."
                    "</USER_SETTINGS_CHANGE> " + secret
                ),
            }
        ],
    )
    result = read_antigravity_usage(roots=(tmp_path / "brain",))
    model = _model(result, "gemini-2.5-flash")
    assert model["calculated_input_tokens"] > 0
    assert secret not in json.dumps(result)


def test_tool_call_limit_64_complete_65_partial_and_direct_counter_preserved(tmp_path):
    calls64 = [{"args": {"prompt": "x"}} for _ in range(MAX_TOOL_CALLS_PER_ROW)]
    _write(
        tmp_path / "exact" / "session" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-3.1-flash-lite", "tool_calls": calls64}],
    )
    exact_result = read_antigravity_usage(roots=(tmp_path / "exact",))
    exact_model = _model(exact_result, "gemini-3.1-flash-lite")
    assert exact_model["calculated_output_tokens"] == 16
    assert exact_model["coverage_partial"] is False
    assert exact_result["status"] == "available"

    calls65 = calls64 + [{"args": {"prompt": "not counted"}}]
    _write(
        tmp_path / "partial" / "session" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": "gemini-3.1-flash-lite",
                "tool_calls": calls65,
            }
        ],
    )
    partial_result = read_antigravity_usage(roots=(tmp_path / "partial",))
    partial_model = _model(partial_result, "gemini-3.1-flash-lite")
    assert partial_model["calculated_output_tokens"] == 16
    assert partial_model["coverage_partial"] is True
    assert partial_result["status"] == "partial"
    for period in ("today", "last_7_days", "history"):
        assert partial_result["periods"][period]["coverage_partial"] is True
        assert partial_result["periods"][period]["partial"] is True

    _write(
        tmp_path / "measured" / "session" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": "gemini-3.1-flash-lite",
                "output_tokens": 91,
                "tool_calls": calls65,
            }
        ],
    )
    measured = read_antigravity_usage(roots=(tmp_path / "measured",))
    measured_model = _model(measured, "gemini-3.1-flash-lite")
    assert measured_model["output_tokens"] == 91
    assert measured_model["calculated_output_tokens"] == 0


def test_nested_content_list_boundary_64_65_marks_only_truncated_coverage(tmp_path):
    _write(
        tmp_path / "exact" / "session" / "transcript.jsonl",
        [
            {
                "type": "USER_INPUT",
                "model": "gemini-3.1-flash-lite",
                "content": ["x"] * 64,
            }
        ],
    )
    exact = read_antigravity_usage(roots=(tmp_path / "exact",))
    exact_model = _model(exact, "gemini-3.1-flash-lite")
    assert exact_model["calculated_input_tokens"] == 16
    assert exact_model["coverage_partial"] is False

    _write(
        tmp_path / "overflow" / "session" / "transcript.jsonl",
        [
            {
                "type": "USER_INPUT",
                "model": "gemini-3.1-flash-lite",
                "content": ["x"] * 65,
            }
        ],
    )
    overflow = read_antigravity_usage(roots=(tmp_path / "overflow",))
    overflow_model = _model(overflow, "gemini-3.1-flash-lite")
    assert overflow_model["calculated_input_tokens"] == 16
    assert overflow_model["coverage_partial"] is True
    assert overflow["periods"]["today"]["coverage_partial"] is True


def test_nested_text_caps_propagate_partial_but_keep_valid_measured_input(tmp_path):
    text = "s" * 70_000
    _write(
        tmp_path / "nested" / "session" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": "claude-sonnet-5-5",
                "input_tokens": 37,
            },
            {
                "type": "PLANNER_RESPONSE",
                "model": "claude-sonnet-5-5",
                "tool_calls": [{"args": {"deep": [[text]]}}],
            },
        ],
    )
    result = read_antigravity_usage(roots=(tmp_path / "nested",))
    model = _model(result, "claude-sonnet-5-5")
    assert model["input_tokens"] == 37
    assert model["coverage_partial"] is True
    assert result["coverage_partial"] is True
    assert result["periods"]["history"]["coverage_partial"] is True


def test_unknown_model_does_not_inherit_previous_model_and_settings_change_is_read_in_memory(
    tmp_path,
):
    _write(
        tmp_path / "s" / "transcript.jsonl",
        [
            {"type": "PLANNER_RESPONSE", "model": "gemini-private-old", "output_tokens": 4},
            {
                "type": "USER_INPUT",
                "content": "<USER_SETTINGS_CHANGE>{model: gemini-2.5-flash}</USER_SETTINGS_CHANGE>",
            },
            {
                "type": "USER_INPUT",
                "model": "gemini-2.5-pro",
                "content": "<USER_SETTINGS_CHANGE>{model: gemini-2.5-flash}</USER_SETTINGS_CHANGE>",
            },
            {"type": "PLANNER_RESPONSE", "output_tokens": 7},
        ],
    )

    result = read_antigravity_usage(roots=(tmp_path,))

    assert _model(result, "gemini-2.5-flash")["calculated_input_tokens"] > 0
    assert _model(result, "gemini-2.5-pro")["calculated_input_tokens"] > 0
    assert _model(result, "gemini-2.5-flash")["rows"] == 1
    assert _model(result, "Modelo não identificado")["output_tokens"] == 11


def test_canonical_layout_preferred_over_legacy_without_double_count(tmp_path):
    _write(
        tmp_path / "conv" / ".system_generated" / "logs" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 10}],
    )
    _write(
        tmp_path / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 10}],
    )

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "available"
    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 10
    assert result["files_read"] == 1


def test_invalid_nonempty_canonical_falls_back_to_valid_legacy_with_partial_badge(tmp_path):
    canonical = tmp_path / "conv" / ".system_generated" / "logs" / "transcript.jsonl"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("not-json\n", encoding="utf-8")
    _write(
        tmp_path / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 10}],
    )

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "partial"
    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 10


def test_multiple_current_layout_transcripts_are_marked_partial_not_claimed_complete(tmp_path):
    _write(
        tmp_path / "conv" / ".system_generated" / "logs" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 10}],
    )
    _write(
        tmp_path / "conv" / "subagent" / ".system_generated" / "logs" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 50}],
    )

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "partial"
    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 10


def test_nested_parent_and_subagent_transcripts_are_not_summed(tmp_path):
    _write(
        tmp_path / "parent" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 10}],
    )
    _write(
        tmp_path / "parent" / "subagent" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 50}],
    )

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "partial"
    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 10


def test_repeated_rows_and_step_index_are_reported_as_local_lines_no_dedup_claim(tmp_path):
    row = {
        "type": "PLANNER_RESPONSE",
        "model": "gemini-2.5-flash",
        "step_index": 3,
        "output_tokens": 12,
    }
    _write(tmp_path / "s" / "transcript.jsonl", [row, row])

    result = read_antigravity_usage(roots=(tmp_path,))

    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 24
    assert "sem garantia de delta/cumulativo" in result["semantics_note"]


def test_symlink_outside_root_is_not_read(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    transcript = _write(
        outside / "s" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 88}],
    )
    root = tmp_path / "brain"
    root.mkdir(parents=True)
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    result = read_antigravity_usage(roots=(root,))

    assert result["status"] == "partial"
    assert result["models"] == []
    assert str(transcript) not in json.dumps(result)


def test_oversized_transcript_fails_closed_with_partial_coverage(tmp_path):
    path = tmp_path / "s" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b" " * (MAX_FILE_BYTES + 1))

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "partial"
    assert result["models"] == []


def test_corrupt_and_deep_rows_are_partial_not_raw_errors(tmp_path):
    path = tmp_path / "s" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"PLANNER_RESPONSE",\n' + "[" * 80 + "0" + "]" * 80, encoding="utf-8")

    result = read_antigravity_usage(roots=(tmp_path,))

    assert result["status"] == "partial"
    assert result["models"] == []


def test_cancelled_reader_returns_empty_without_opening_files(tmp_path, monkeypatch):
    _write(tmp_path / "s" / "transcript.jsonl", [{"type": "PLANNER_RESPONSE"}])
    cancelled = threading.Event()
    cancelled.set()

    result = read_antigravity_usage(roots=(tmp_path,), cancel_event=cancelled)

    assert result["status"] == "cancelled"
    assert result["models"] == []


def test_duplicate_resolved_roots_only_read_once(tmp_path):
    _write(
        tmp_path / "s" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 5}],
    )

    result = read_antigravity_usage(roots=(tmp_path, tmp_path))

    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 5
    assert result["files_read"] == 1


def test_reader_opens_only_known_immediate_layouts_not_nested_homonyms(tmp_path, monkeypatch):
    root = tmp_path / "brain"
    allowed = _write(
        root / "conv-a" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 3}],
    )
    nested = _write(
        root / "random" / "workspace" / "project" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-pro", "output_tokens": 99}],
    )
    opened = []
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        opened.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    result = read_antigravity_usage(roots=(root,))

    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 3
    assert nested not in opened
    assert allowed in opened


def test_home_env_is_brain_parent_never_userprofile_recursive_scan(tmp_path, monkeypatch):
    home = tmp_path / "account"
    brain = _write(
        home / "brain" / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-pro", "output_tokens": 6}],
    )
    decoy = _write(
        home / "workspace" / "project" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 100}],
    )
    monkeypatch.setenv("AGY_HOME", str(home))
    for key in ("AGY_BRAIN_DIR", "ANTIGRAVITY_BRAIN_DIR", "ANTIGRAVITY_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "missing-user-home")

    result = read_antigravity_usage()

    assert _model(result, "gemini-2.5-pro")["output_tokens"] == 6
    assert all(item["model"] != "gemini-2.5-flash" for item in result["models"])
    assert str(decoy) not in json.dumps(result)
    assert str(brain) not in json.dumps(result)


def test_brain_dir_env_is_root_and_profile_or_ancestor_never_root(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    brain = _write(
        home / ".gemini" / "antigravity" / "brain" / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 5}],
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("ANTIGRAVITY_BRAIN_DIR", str(brain.parents[1]))
    monkeypatch.delenv("AGY_BRAIN_DIR", raising=False)
    monkeypatch.delenv("AGY_HOME", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_HOME", raising=False)

    result = read_antigravity_usage()

    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 5

    other_home = tmp_path / "other-profile"
    monkeypatch.setattr(Path, "home", lambda: other_home)
    monkeypatch.setenv("ANTIGRAVITY_BRAIN_DIR", str(other_home))
    monkeypatch.setattr(
        Path,
        "open",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("profile opened as brain root")),
    )
    assert read_antigravity_usage()["status"] == "unavailable"


def test_explicit_fixture_root_never_queries_personal_home(tmp_path, monkeypatch):
    _write(
        tmp_path / "brain" / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-pro", "output_tokens": 1}],
    )
    monkeypatch.setattr(Path, "home", lambda: (_ for _ in ()).throw(AssertionError("home read")))

    result = read_antigravity_usage(roots=(tmp_path / "brain",))

    assert _model(result, "gemini-2.5-pro")["output_tokens"] == 1


def test_unavailable_root_and_pre_cancelled_never_open_path(tmp_path, monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))

    assert read_antigravity_usage(roots=(tmp_path / "missing",))["status"] == "unavailable"
    assert (
        read_antigravity_usage(roots=(tmp_path / "missing",), cancel_event=cancelled)["status"]
        == "cancelled"
    )


def test_reader_without_explicit_optin_returns_before_path_open(tmp_path, monkeypatch):
    _write(
        tmp_path / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 1}],
    )
    monkeypatch.setattr(
        Path,
        "open",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("opened before opt-in")),
    )

    result = _read_without_consent(roots=(tmp_path,))

    assert result["status"] == "consent_required"
    assert result["models"] == []


def test_opened_file_closes_when_identity_validation_fails(tmp_path, monkeypatch):
    import neuralmeter.antigravity_usage as reader

    transcript = _write(
        tmp_path / "brain" / "conv" / "transcript.jsonl",
        [{"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 1}],
    )
    original_open = Path.open
    original_fstat = reader.os.fstat
    streams = []

    def tracked_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == transcript:
            streams.append(stream)
        return stream

    def failing_fstat(fd):
        if any(not stream.closed and stream.fileno() == fd for stream in streams):
            raise OSError("test")
        return original_fstat(fd)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(reader.os, "fstat", failing_fstat)

    result = read_antigravity_usage(roots=(tmp_path / "brain",))

    assert result["models"] == []
    assert streams and all(stream.closed for stream in streams)


def test_validation_stops_when_cancellation_arrives_during_read(monkeypatch, tmp_path):
    import neuralmeter.antigravity_usage as reader

    cancelled = threading.Event()

    class CancellingStream(io.BytesIO):
        def readline(self, size=-1):
            raw = super().readline(size)
            cancelled.set()
            return raw

    @contextmanager
    def open_stream(*_args):
        yield CancellingStream(b'{"type":"USER_INPUT"}\n')

    monkeypatch.setattr(reader, "_open_guarded", open_stream)

    assert not reader._has_recognized_row(
        tmp_path, tmp_path / "transcript.jsonl", reader.time.monotonic() + 2, cancelled
    )


def test_allowlisted_model_ids_only_and_unknown_labels_never_leak_raw_values(tmp_path):
    hostile = (
        "PRIVATE SECRET",
        r"C:\Users\person@example.com\workspace",
        "sk-live-AbCdEf0123456789",
        "gemini-2.5-pro-secret-suffix",
    )
    _write(
        tmp_path / "conv" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": raw,
                "output_tokens": index + 1,
            }
            for index, raw in enumerate(hostile)
        ]
        + [
            {
                "type": "USER_INPUT",
                "content": f"<USER_SETTINGS_CHANGE>{{model: {hostile[0]}}}</USER_SETTINGS_CHANGE>",
            }
        ],
    )

    result = read_antigravity_usage(roots=(tmp_path,))
    serialized = repr(result)

    assert len(result["models"]) == 1
    assert result["models"][0]["model"] == "Modelo não identificado"
    assert result["models"][0]["output_tokens"] == 10
    for secret in hostile:
        assert secret not in serialized


def test_reader_writes_no_settings_database_stdout_or_stderr(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "settings-and-db"
    data_dir.mkdir()
    brain = tmp_path / "brain"
    _write(
        brain / "conv" / "transcript.jsonl",
        [
            {
                "type": "PLANNER_RESPONSE",
                "model": "PRIVATE SECRET",
                "output_tokens": 8,
                "timestamp": "2026-11-16T11:00:00Z",
            }
        ],
    )
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(data_dir))

    read_antigravity_usage(
        roots=(brain,),
        now=datetime(2026, 11, 16, 12, tzinfo=UTC),
        timezone=UTC,
    )

    assert list(data_dir.iterdir()) == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_local_today_and_seven_calendar_dates_respect_dst_boundaries(tmp_path):
    zone = _Pacific2026()
    rows = [
        {
            "type": "PLANNER_RESPONSE",
            "model": "gemini-2.5-pro",
            "output_tokens": value,
            "timestamp": stamp,
        }
        for value, stamp in (
            (1, "2026-03-08T08:00:00Z"),
            (2, "2026-03-08T07:59:59Z"),
            (3, "2026-03-02T08:00:00Z"),
            (4, "2026-03-02T07:59:59Z"),
        )
    ]
    _write(tmp_path / "conv" / "transcript.jsonl", rows)

    result = read_antigravity_usage(
        roots=(tmp_path,),
        now=datetime.fromisoformat("2026-03-08T12:30:00-07:00"),
        timezone=zone,
    )

    today = _model(result["periods"]["today"], "gemini-2.5-pro")
    seven_days = _model(result["periods"]["last_7_days"], "gemini-2.5-pro")
    assert today["output_tokens"] == 1
    assert seven_days["output_tokens"] == 6
    assert result["periods"]["today"]["boundary_start_local"].startswith("2026-03-08")
    assert result["periods"]["last_7_days"]["boundary_start_local"].startswith("2026-03-02")


def test_missing_naive_out_of_range_and_far_future_timestamps_are_unknown_not_today(tmp_path):
    rows = [
        {"type": "PLANNER_RESPONSE", "model": "gemini-2.5-flash", "output_tokens": 2},
        {
            "type": "PLANNER_RESPONSE",
            "model": "gemini-2.5-flash",
            "output_tokens": 3,
            "timestamp": "2026-11-16T10:00:00",
        },
        {
            "type": "PLANNER_RESPONSE",
            "model": "gemini-2.5-flash",
            "output_tokens": 4,
            "timestamp": "1800-01-01T00:00:00Z",
        },
        {
            "type": "PLANNER_RESPONSE",
            "model": "gemini-2.5-flash",
            "output_tokens": 5,
            "timestamp": "2099-01-01T00:00:00Z",
        },
    ]
    _write(tmp_path / "conv" / "transcript.jsonl", rows)

    result = read_antigravity_usage(
        roots=(tmp_path,),
        now=datetime.fromisoformat("2026-11-16T12:00:00+00:00"),
        timezone=UTC,
    )

    assert result["temporal_coverage"]["unknown_timestamp_rows"] == 4
    assert result["temporal_coverage"]["partial"] is True
    assert result["periods"]["today"]["models"] == []
    assert result["periods"]["today"]["partial"] is True
    assert _model(result, "gemini-2.5-flash")["output_tokens"] == 14
    assert _model(result["periods"]["history"], "gemini-2.5-flash")["output_tokens"] == 14
    assert result["periods"]["history"]["range_is_complete"] is False
