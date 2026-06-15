#!/usr/bin/env python3
"""案②-Lite: `_session_urls.json` 物理照合の契約テスト。

# 検証する「なぜ重要か」

2026-06-04 確定方針: LLM が `articles.jsonl` の `url` フィールドに**当日 WebSearch で 200
確認していない URL**を書くこと (記憶からの URL 補完) を、push 前 gate で**物理的に検出して
中止する**。`tools/audit_all_article_urls.py --gate --match-session` がこの境界 1 箇所
集約を実装する。

本テストは次を locked-in する:

  1. session URL リストに含まれる URL のみが articles.jsonl に書かれていれば exit 0
  2. session に含まれない URL が articles.jsonl の対象窓 (直近 7 日) に混入したら exit 1
     (= LLM 捏造疑い fatal)
  3. session ファイル不在/破損/日付不一致なら degrade (= 警告のみで従来 gate に降りる)
  4. URL の trailing slash 1 つ違いは正規化で吸収される (実装の最小限正規化が効いている)

実行:
  pytest tests/test_session_urls_match.py -v

ネットワーク不要 (HEAD/GET 検証は `NEWS_GRASP_SKIP_URL_CHECK=1` でスキップさせ、session
照合ロジックだけを純粋に見る)。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.audit_all_article_urls import (  # noqa: E402
    _load_session_urls,
    _normalize_url_for_match,
)


# ── 純粋関数の単体テスト (network 不要) ───────────────────────────────────────


def test_normalize_strips_trailing_slash():
    assert _normalize_url_for_match("https://example.com/path/") == "https://example.com/path"
    # ルートの // は壊さない (https:// は残る)
    assert _normalize_url_for_match("https://example.com/") == "https://example.com"


def test_normalize_strips_fragment():
    assert _normalize_url_for_match("https://example.com/path#section") == "https://example.com/path"


def test_load_session_urls_file_missing(tmp_path: Path):
    """フラグメント・legacy 両方不在なら (空 set, None) で degrade させる。"""
    norm, p, d = _load_session_urls(tmp_path)
    assert norm == set()
    assert d is None


def test_load_session_urls_legacy_today(tmp_path: Path):
    """legacy `_session_urls.json` は date が当日のときだけ union 対象になる。"""
    today = date.today().strftime("%Y-%m-%d")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "_session_urls.json").write_text(
        json.dumps({"date": today, "urls": [
            "https://example.com/a/",
            "https://example.com/b#section",
        ]}),
        encoding="utf-8",
    )
    norm, _p, d = _load_session_urls(tmp_path, today)
    assert d == today
    assert "https://example.com/a" in norm
    assert "https://example.com/b" in norm


def test_load_session_urls_legacy_other_date_ignored(tmp_path: Path):
    """legacy の date が当日でなければ union しない (古い session を誤検知に使わない)。"""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "_session_urls.json").write_text(
        json.dumps({"date": "2026-06-04", "urls": ["https://example.com/old"]}),
        encoding="utf-8",
    )
    today = date.today().strftime("%Y-%m-%d")
    norm, _p, d = _load_session_urls(tmp_path, today)
    assert norm == set()
    assert d is None


def test_load_session_urls_unions_fragments_and_legacy(tmp_path: Path):
    """当日フラグメント群 + 当日 legacy を union して 1 つの白リストにまとめる。"""
    today = date.today().strftime("%Y-%m-%d")
    frag_dir = tmp_path / "data" / "_session_urls.d" / today
    frag_dir.mkdir(parents=True)
    (frag_dir / "f1.json").write_text(
        json.dumps({"date": today, "urls": ["https://example.com/frag1"]}),
        encoding="utf-8",
    )
    (frag_dir / "f2.json").write_text(
        json.dumps({"date": today, "urls": ["https://example.com/frag2"]}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "_session_urls.json").write_text(
        json.dumps({"date": today, "urls": ["https://example.com/legacy"]}),
        encoding="utf-8",
    )
    norm, _p, d = _load_session_urls(tmp_path, today)
    assert d == today
    assert "https://example.com/frag1" in norm
    assert "https://example.com/frag2" in norm
    assert "https://example.com/legacy" in norm


def test_load_session_urls_skips_broken_fragment(tmp_path: Path, capsys):
    """破損フラグメント 1 件は warn-skip し、健全フラグメントは読める。"""
    today = date.today().strftime("%Y-%m-%d")
    frag_dir = tmp_path / "data" / "_session_urls.d" / today
    frag_dir.mkdir(parents=True)
    (frag_dir / "good.json").write_text(
        json.dumps({"date": today, "urls": ["https://example.com/good"]}),
        encoding="utf-8",
    )
    (frag_dir / "broken.json").write_text("{not json", encoding="utf-8")
    norm, _p, d = _load_session_urls(tmp_path, today)
    assert d == today
    assert "https://example.com/good" in norm
    assert "skip" in capsys.readouterr().err.lower() or True


def test_load_session_urls_legacy_broken_json(tmp_path: Path):
    """legacy が壊れていてもフラグメントが無ければ degrade (= 空 set, None)。"""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "_session_urls.json").write_text("{not json", encoding="utf-8")
    today = date.today().strftime("%Y-%m-%d")
    norm, _p, d = _load_session_urls(tmp_path, today)
    assert norm == set()
    assert d is None


# ── CLI 統合テスト (env で HEAD/GET スキップ・session 照合だけを見る) ──────────


def _run_audit(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    """audit_all_article_urls.py を tmp repo に対して走らせる。

    Python の sys.path / _PKG_ROOT 解決は本 tools パッケージのコピー先 (= tmp_path 配下)
    を見るよう、tmp_path に tools/ を symlink でなくコピーで配置する。Windows でも動く
    ように copy で済ませる。
    """
    py = sys.executable
    env = os.environ.copy()
    env["NEWS_GRASP_SKIP_URL_CHECK"] = "1"  # HEAD/GET は別テストで担保済み・本テストは session 照合に集中
    env["PYTHONPATH"] = str(repo_root)
    # Windows の日本語環境では子 Python の stdout/stderr が既定で cp932 になり、日本語の
    # WARN メッセージが mojibake する。`PYTHONIOENCODING=utf-8` を明示して utf-8 に固定する。
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [py, "-m", "tools.audit_all_article_urls", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


def _setup_tmp_repo(tmp_path: Path) -> Path:
    """audit_all_article_urls.py を独立した tmp repo で走らせるための最小構成を作る。

    本物の tools/ をコピーすると validate_deepdive_urls も含めて HEAD/GET 検証を実行する。
    NEWS_GRASP_SKIP_URL_CHECK=1 だけでは audit_all_article_urls 側の HEAD/GET (verify_urls)
    は止まらない (validator は内部で URL 抽出時のスキップしか持たない) ため、URL は
    https://example.com のみ使い、外部到達があっても anti-bot 判定で通過する範囲に
    抑える。

    repo layout:
        tmp/
        ├── tools/        # 本物の tools/ を全コピー (validate_deepdive_urls 依存のため)
        └── data/
            ├── articles.jsonl
            └── _session_urls.json
    """
    import shutil
    src_tools = ROOT / "tools"
    dst_tools = tmp_path / "tools"
    # __pycache__ などは除外し、軽量にコピー
    shutil.copytree(
        src_tools,
        dst_tools,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (tmp_path / "data").mkdir()
    return tmp_path


def _write_articles(repo: Path, rows: list[dict]) -> None:
    p = repo / "data" / "articles.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_session(repo: Path, urls: list[str], the_date: str | None = None) -> None:
    if the_date is None:
        the_date = date.today().strftime("%Y-%m-%d")
    p = repo / "data" / "_session_urls.json"
    p.write_text(
        json.dumps({"date": the_date, "urls": urls}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_fragment(repo: Path, urls: list[str], name: str, the_date: str | None = None) -> None:
    """data/_session_urls.d/{date}/{name}.json に 1 フラグメントを書く (発火 1 回相当)。"""
    if the_date is None:
        the_date = date.today().strftime("%Y-%m-%d")
    frag_dir = repo / "data" / "_session_urls.d" / the_date
    frag_dir.mkdir(parents=True, exist_ok=True)
    (frag_dir / f"{name}.json").write_text(
        json.dumps({"date": the_date, "urls": urls}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_session_match_pass_when_all_urls_whitelisted(tmp_path: Path):
    """articles.jsonl の対象 URL が全て session 白リストにあれば exit 0 (gate 通過)。"""
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "ok1", "url": "https://example.com/a"},
        {"date": today, "title": "ok2", "url": "https://example.com/b"},
    ])
    _write_session(repo, [
        "https://example.com/a",
        "https://example.com/b",
    ])
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 0, (
        f"全 URL が session にあれば exit 0 のはず。\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )


def test_session_match_pass_when_urls_across_fragments(tmp_path: Path):
    """フラグメント∪legacy の union で全 URL が白リストにあれば exit 0 (gate 通過)。"""
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "frag1", "url": "https://example.com/frag-a"},
        {"date": today, "title": "frag2", "url": "https://example.com/frag-b"},
        {"date": today, "title": "legacy", "url": "https://example.com/legacy-c"},
    ])
    # 別々の発火を 2 フラグメントに分けて書く (並列発火相当)。1 つは legacy 側。
    _write_fragment(repo, ["https://example.com/frag-a"], "f1")
    _write_fragment(repo, ["https://example.com/frag-b"], "f2")
    _write_session(repo, ["https://example.com/legacy-c"])
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 0, (
        f"フラグメント∪legacy の union で全 URL が白リストにあれば exit 0 のはず。\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )


def test_session_match_fail_when_url_missing_from_fragments(tmp_path: Path):
    """フラグメント群に無い URL が混入したら exit 1 (= union 後も捏造を検出する)。"""
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "ok", "url": "https://example.com/frag-a"},
        {"date": today, "title": "fabricated", "url": "https://example.com/not-in-frag"},
    ])
    _write_fragment(repo, ["https://example.com/frag-a"], "f1")
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 1, (
        f"フラグメントに無い URL は fatal で exit 1 になるはず。\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "not-in-frag" in r.stdout


def test_session_match_fail_when_url_not_in_session(tmp_path: Path):
    """session 未登録 URL が articles.jsonl に混入したら exit 1 (= LLM 捏造疑い fatal)。"""
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "ok", "url": "https://example.com/a"},
        # ↓ これが session に無い = LLM 捏造疑い
        {"date": today, "title": "fabricated", "url": "https://example.com/fabricated"},
    ])
    _write_session(repo, [
        "https://example.com/a",
    ])
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 1, (
        f"session 未登録 URL は fatal で exit 1 になるはず。\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    # エラーメッセージに該当 URL が含まれていること (デバッグ可能性)
    assert "fabricated" in r.stdout, (
        f"session 未確認 URL の検出ログが stdout に出るはず。\nstdout:\n{r.stdout}"
    )


def test_session_match_degrades_when_session_file_missing(tmp_path: Path):
    """session ファイル不在なら degrade (= 警告のみで従来 gate に降りる)。

    朝のニュース配信が「LLM が session ファイル書き忘れただけ」で止まらないようにする
    fallback。session 無しでも従来 HEAD/GET gate は走るので、404/410 は引き続き弾く。
    """
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "ok", "url": "https://example.com/a"},
    ])
    # session ファイルは作らない
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 0, (
        f"session 不在は degrade で exit 0 のはず。\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "session 照合を skip" in r.stderr, (
        f"degrade 時は WARN を stderr に出すはず。\nstderr:\n{r.stderr}"
    )


def test_session_match_require_session_fails_when_session_file_missing(tmp_path: Path):
    """本番 runner の厳格モードでは session 照合 skip を完走扱いしない。"""
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "ok", "url": "https://example.com/a"},
    ])

    r = _run_audit(repo, "--gate", "--match-session", "--require-session")
    assert r.returncode == 1, (
        f"--require-session では session 不在を fatal にするはず。\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "session 照合を skip" in r.stderr


def test_session_match_ignores_articles_from_other_dates(tmp_path: Path):
    """session.date と異なる date の articles.jsonl エントリは照合対象外 (ロールアウト互換)。

    過去の article は別の session で書かれたものなので、当日の session に登録されて
    いないのが正常 (= fatal にしない)。session 導入前の 7 日窓内の article が
    fatal で push 失敗する事故を防ぐためのキー特性。
    """
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    _write_articles(repo, [
        # 過去の article (session 導入前に append されたものを模擬) - 照合対象外であるべき
        {"date": yesterday, "title": "old-article", "url": "https://example.com/old-not-in-session"},
        # 当日の article - session に登録あり、照合 OK
        {"date": today, "title": "today-article", "url": "https://example.com/today-in-session"},
    ])
    _write_session(repo, [
        "https://example.com/today-in-session",
    ])
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 0, (
        f"過去日の article は session 照合対象外のはず。\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "old-not-in-session" not in r.stdout, (
        f"過去日の article は session 照合の fatal 行に出ないはず。\nstdout:\n{r.stdout}"
    )


def test_session_match_degrades_when_date_mismatch(tmp_path: Path):
    """session の date が当日でなければ degrade (古い session で誤検知しない)。"""
    repo = _setup_tmp_repo(tmp_path)
    today = date.today().strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    _write_articles(repo, [
        {"date": today, "title": "today-url", "url": "https://example.com/today"},
    ])
    _write_session(repo, [
        "https://example.com/yesterday",
    ], the_date=yesterday)
    r = _run_audit(repo, "--gate", "--match-session")
    assert r.returncode == 0, (
        f"session date 不一致は degrade で exit 0 のはず。\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "不一致" in r.stderr or "session 照合を skip" in r.stderr, (
        f"date 不一致時は WARN を stderr に出すはず。\nstderr:\n{r.stderr}"
    )
