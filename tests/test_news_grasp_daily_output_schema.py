"""News-Grasp 日次出力 schema の実体と recovery 判定境界を検証する。"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


SCHEMA_FILES = (
    "news_grasp_daily_reporter_output.schema.json",
    "news_grasp_daily_reporter_shard_output.schema.json",
    "news_grasp_daily_editor_output.schema.json",
    "news_grasp_daily_deepdive_output.schema.json",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _assert_closed_schema(node: object, pointer: str) -> None:
    if not isinstance(node, dict):
        return

    if node.get("type") == "object":
        properties = node.get("properties")
        assert isinstance(properties, dict), f"{pointer}: properties must be an object"
        assert node.get("additionalProperties") is False, (
            f"{pointer}: additionalProperties must be false"
        )
        required = node.get("required")
        assert isinstance(required, list), f"{pointer}: required must be a list"
        assert set(required) == set(properties), (
            f"{pointer}: required/properties mismatch"
        )

        for name, child in properties.items():
            _assert_closed_schema(child, f"{pointer}/properties/{name}")

    if node.get("type") == "array":
        _assert_closed_schema(node.get("items"), f"{pointer}/items")

    for name, child in node.get("$defs", {}).items():
        _assert_closed_schema(child, f"{pointer}/$defs/{name}")


def _copy_schemas(repo_root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in SCHEMA_FILES:
        shutil.copy2(repo_root / "schemas" / name, destination / name)


def _schema_rejection_event(message: str) -> dict[str, object]:
    return {
        "type": "turn.failed",
        "error": {"message": message},
    }


def _write_events(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _make_junction(link: Path, target: Path) -> None:
    command = (
        "$ErrorActionPreference='Stop'; "
        f"New-Item -ItemType Junction -Path {str(link)!r} -Target {str(target)!r} | Out-Null"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        pytest.fail(f"junction fixture creation failed: {completed.stderr}")


def test_daily_output_schemas_are_closed_and_explicit() -> None:
    """4出力schemaの全objectがpropertiesと閉鎖条件を持つ。"""

    root = _repo_root()
    for name in SCHEMA_FILES:
        schema_path = root / "schemas" / name
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        _assert_closed_schema(schema, f"{name}#")


def test_validate_daily_output_schemas_accepts_the_repository_schema_set() -> None:
    content = importlib.import_module("tools.news_grasp_daily_content")

    assert content.validate_daily_output_schemas(_repo_root()) is None


def test_validate_daily_output_schemas_rejects_missing_properties(tmp_path: Path) -> None:
    content = importlib.import_module("tools.news_grasp_daily_content")
    schema_root = tmp_path / "schemas"
    _copy_schemas(_repo_root(), schema_root)
    editor_path = schema_root / "news_grasp_daily_editor_output.schema.json"
    editor_schema = json.loads(editor_path.read_text(encoding="utf-8"))
    editor_schema["properties"]["inputs"].pop("properties", None)
    editor_path.write_text(
        json.dumps(editor_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(content.DailyContentError):
        content.validate_daily_output_schemas(tmp_path)


def test_static_check_uses_the_daily_schema_validator(monkeypatch, tmp_path: Path) -> None:
    content = importlib.import_module("tools.news_grasp_daily_content")
    gate = importlib.import_module("tools.news_grasp_daily_gate")
    calls: list[Path] = []
    _copy_schemas(_repo_root(), tmp_path / "schemas")
    real_validator = content.validate_daily_output_schemas

    def spy(repo_root: Path) -> None:
        calls.append(Path(repo_root).resolve())
        real_validator(repo_root)

    monkeypatch.setattr(content, "validate_daily_output_schemas", spy, raising=False)
    gate._default_static_check(repo_root=tmp_path)

    assert calls == [tmp_path.resolve()]


@pytest.mark.parametrize(
    ("case", "rows", "expected"),
    [
        (
            "exact-terminal-schema-rejection",
            [
                {"type": "turn.started"},
                _schema_rejection_event(
                    json.dumps(
                        {
                            "status": 400,
                            "error": {"code": "invalid_json_schema"},
                        }
                    )
                ),
            ],
            True,
        ),
        (
            "raw-text-is-not-confirmation",
            [
                _schema_rejection_event("HTTP 400 invalid_json_schema"),
            ],
            False,
        ),
        (
            "other-400-is-not-confirmation",
            [
                _schema_rejection_event(
                    json.dumps(
                        {"status_code": 400, "error": {"code": "other_error"}}
                    )
                ),
            ],
            False,
        ),
        (
            "other-status-is-not-confirmation",
            [
                _schema_rejection_event(
                    json.dumps(
                        {
                            "status": 500,
                            "error": {"code": "invalid_json_schema"},
                        }
                    )
                ),
            ],
            False,
        ),
        (
            "malformed-json-is-not-confirmation",
            [_schema_rejection_event('{"status_code":400')],
            False,
        ),
        (
            "completed-after-failure-is-not-confirmation",
            [
                _schema_rejection_event(
                    json.dumps(
                        {
                            "status_code": 400,
                            "error": {"code": "invalid_json_schema"},
                        }
                    )
                ),
                {"type": "turn.completed"},
            ],
            False,
        ),
        (
            "timeout-is-not-confirmation",
            [{"type": "turn.timeout", "error": {"message": "timeout"}}],
            False,
        ),
    ],
)
def test_confirmed_schema_rejection_requires_exact_terminal_event(
    tmp_path: Path,
    case: str,
    rows: list[dict[str, object]],
    expected: bool,
) -> None:
    content = importlib.import_module("tools.news_grasp_daily_content")
    events_path = tmp_path / f"{case}.events.jsonl"
    _write_events(events_path, rows)

    assert content._is_confirmed_schema_rejection(events_path) is expected


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point boundary")
def test_confirmed_schema_rejection_rejects_reparse_point(tmp_path: Path) -> None:
    content = importlib.import_module("tools.news_grasp_daily_content")
    target = tmp_path / "events-target"
    target.mkdir()
    events_path = target / "editor.events.jsonl"
    _write_events(
        events_path,
        [
            _schema_rejection_event(
                json.dumps(
                    {
                        "status": 400,
                        "error": {"code": "invalid_json_schema"},
                    }
                )
            )
        ],
    )
    link = tmp_path / "events-link"
    _make_junction(link, target)

    assert content._is_confirmed_schema_rejection(link / events_path.name) is False


def test_confirmed_schema_rejection_rejects_events_over_16_mib_with_bounded_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """eventsは16MiBを超えた時点で拒否し、全量をメモリへ読まない。"""

    content = importlib.import_module("tools.news_grasp_daily_content")
    max_event_bytes = 16 * 1024 * 1024
    events_path = tmp_path / "oversized.events.jsonl"
    prefix = b'{"type":"turn.started"}\n'
    terminal = (
        json.dumps(
            {
                "type": "turn.failed",
                "error": {
                    "message": json.dumps(
                        {
                            "status": 400,
                            "error": {"code": "invalid_json_schema"},
                        }
                    )
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    with events_path.open("wb") as handle:
        chunk = prefix * 4096
        while handle.tell() <= max_event_bytes:
            handle.write(chunk)
        handle.write(terminal)
    assert events_path.stat().st_size > max_event_bytes

    read_sizes: list[int] = []
    real_open = Path.open

    class _ReadSpy:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._handle.__exit__(exc_type, exc_value, traceback)

        def read(self, size: int = -1):
            read_sizes.append(size)
            return self._handle.read(size)

        def __getattr__(self, name: str):
            return getattr(self._handle, name)

    def open_spy(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self == events_path:
            return _ReadSpy(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_spy)

    assert content._is_confirmed_schema_rejection(events_path) is False
    assert all(0 <= size <= max_event_bytes + 1 for size in read_sizes)
