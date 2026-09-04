from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETIRED_BASENAME = "news_grasp_" + "daily_launcher"
RETIRED_MODULE = "tools." + RETIRED_BASENAME
DIRECT_DAILY_COMMAND = (
    r"C:\\Users\\hidek\\AppData\\Local\\Programs\\Python\\Python312\\python.exe "
    "-m tools.news_grasp_direct_runtime daily"
)
TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".pyw",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
EXCLUDED_PARTS = {".git", ".pytest_cache", ".venv", "__pycache__", "build"}


def _active_text_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in TEXT_SUFFIXES
        and not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _contains_retired_module(path: Path) -> bool:
    raw = path.read_bytes()
    return any(
        encoded in raw
        for encoded in (
            RETIRED_MODULE.encode("utf-8"),
            RETIRED_MODULE.encode("utf-16-le"),
            RETIRED_MODULE.encode("utf-16-be"),
        )
    )


def test_retired_daily_entry_is_absent_from_active_product_tree() -> None:
    retired_path = ROOT / "tools" / f"{RETIRED_BASENAME}.py"
    assert not retired_path.exists(), f"retired entry remains executable: {retired_path}"

    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in _active_text_files()
        if _contains_retired_module(path)
    ]
    assert offenders == []


def test_direct_runtime_is_the_single_documented_daily_process() -> None:
    runtime = (ROOT / "tools" / "news_grasp_direct_runtime.py").read_text(
        encoding="utf-8"
    )
    automation = (
        ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"
    ).read_text(encoding="utf-8")

    assert 'sub.add_parser("daily")' in runtime
    assert "def run_daily_mainline(" in runtime
    assert automation.count(DIRECT_DAILY_COMMAND) == 1
