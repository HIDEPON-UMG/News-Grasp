#!/usr/bin/env python3
"""DeepDive md 内の URL を実機 HEAD/GET で生存検証する境界モジュール。

# なぜこれが必要か (2026-06-03 三菱UFJ FX_Monthly 捏造事故)

`https://www.bk.mufg.jp/report/hconwnew/FX_Monthly_0529.pdf` が DeepDive
本文の参考リンクと chart source に書かれ、404 のまま GitHub Pages に公開された。
LLM の記憶で URL を「それっぽく」生成しており、`WebFetch` で実際にアクセスしていない URL が
混入していた。`render_deepdive.py` の `_require_blocks` は必須ブロックの**存在**しか
チェックしていないため、URL の**生存**は素通りしていた。

# 設計 (feedback_check_design_principles の 5 段で構造解決)

- **境界 1 箇所集約 (本命)**: URL 抽出 + HEAD/GET + 判定をこの 1 モジュールに寄せ、
  `render_deepdive.py` の hard-fail パスから必ず通す。本モジュールを経由しない URL
  混入経路をコード上に作らない。
- **契約テスト**: `tests/test_deepdive_urls_live.py` がこのモジュールを呼び全 md を走査。
- **検証分離**: 静的pytestだけは `NEWS_GRASP_SKIP_URL_CHECK=1` で外部通信を分離できる。
  本番 runner は現在日付のURL gate前に親環境の値を消去し、skip証跡を公開証明へ流用しない。

# 検証ロジック

URL → HEAD (10s) → 200-399 = OK / 4xx/5xx = FATAL / network err = GET再試行後も検証不能ならFATAL。
403/405 は anti-bot で HEAD 拒否されるサイト (investing.com 等) があるため、GET 4 KB range
取得で再判定する。これで「無条件 404 = 捏造」だけを残し、bot 拒否の正規 URL を誤検出しない。

# 抽出対象

DeepDive の URL が書きうる箇所は次の 5 種類。すべて単一の AST 走査で網羅する:
1. `## 参考リンク` の `- 説明: https://...` bullets
2. ` ```timeline ` ブロック内の `url` フィールド
3. ` ```relations ` ブロック内の `source` フィールド (本文中の URL)
4. ` ```chart ` ブロック内の `source` フィールド (本文中の URL)
5. ` ```table ` ブロック内の `source` フィールド (本文中の URL)

`related.url` は date から render 側で組み立てるため md には書かれず、検証対象外。

# CLI

```
python -m tools.validate_deepdive_urls digest/DeepDive/2026-06-03-DeepDive.md
python -m tools.validate_deepdive_urls --all  # digest/DeepDive/*.md 全件
```

exit 0 = 全 URL 健全 / exit 1 = 1 件以上 fatal (= 捏造または恒久 404)。
"""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

from tools.safe_public_fetch import resolve_public_http_endpoint, safe_urlopen
from tools.tts import proc
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))


# bot 偽装用 UA。複数 UA で順次探ることで anti-bot を剥がし、隠れた 404 を露出させる。
# 1 段目 Chrome (Windows) → 2 段目 Safari (Mac) の 2 UA を試す。techxplore のように
# urllib + Chrome では 403 を返すが Safari UA + Apache では 404 を出すサイトがあり、
# 1 UA だけで判定すると anti-bot 偽装の裏に隠れた捏造 URL を素通りさせるため。
# (2026-06-03 三菱UFJ FX_Monthly 事故の追加学習・urllib 単独で TechXplore の捏造 URL も発覚)
_UAS = (
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
)
_UA = _UAS[0]  # 1 段目 (旧コードとの互換用)

# 参考リンク bullet 末尾 URL 抽出。`- 説明: https://...` の末尾 URL を拾う。
_REF_BULLET_URL_RE = re.compile(r"(https?://\S+?)[\s\)\]\"　]*$")
_MARKDOWN_LINK_URL_RE = re.compile(r"\]\((https?://[^\s\)]+)\)")

# 任意の文字列内の URL 抽出 (relations/chart/table の source 用)。
_INLINE_URL_RE = re.compile(r"https?://[^\s\)\]\"　]+")

# fenced block 抽出 (render_deepdive.py の extract_blocks と同等の正規表現)。
_FENCED_RE = re.compile(
    r"^```(timeline|relations|chart|table)\s*\r?\n(.*?)\r?\n```\s*$",
    re.MULTILINE | re.DOTALL,
)

# 参考リンク セクション抽出。
_REFS_SECTION_RE = re.compile(
    r"^##\s*参考リンク\s*\r?\n(.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class UrlRef:
    """1 URL 検証単位。location は md 内の出所 (`refs` / `timeline` / `chart` …)。"""
    url: str
    location: str  # "refs" | "timeline" | "relations.source" | "chart.source" | "table.source"


@dataclass(frozen=True)
class UrlVerdict:
    ref: UrlRef
    status: int | None       # HTTP code, または None (network error)
    ok: bool                 # True = 生存確認, False = fatal
    detail: str              # 人間向け説明 (404 / network err / 403→GETで200 等)

    @property
    def verification_status(self) -> str:
        if self.ok:
            return "verified"
        if "ambiguous" in self.detail or "anti-bot" in self.detail:
            return "ambiguous"
        return "fatal"


class DeepDiveUrlError(Exception):
    """DeepDive md に生存しない URL が含まれる。

    `render_deepdive.py` の `_require_blocks` と同じく hard fail させ、捏造 URL を
    含む未完成記事をサイレント公開しない。
    """


def _strip_url_tail(url: str) -> str:
    """URL 末尾のトレーリング句読点 (`.`, `,`, `)`, `」`, `。` 等) を剥がす。

    md パーサが正規表現で URL を抽出するときの定番落とし穴の正規化。
    """
    return url.rstrip(".,;:!?)、。」』”'\"")


def _extract_refs_urls(md_text: str) -> list[UrlRef]:
    """`## 参考リンク` セクションの bullet 末尾 URL を抽出する。"""
    m = _REFS_SECTION_RE.search(md_text)
    if not m:
        return []
    body = m.group(1)
    out: list[UrlRef] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Markdown link は閉じ括弧より内側だけを URL とする。後続の日付注記を
        # URL に混入させると HTTP request 自体が作れず、旧判定が ambiguous OK にした。
        um = _MARKDOWN_LINK_URL_RE.search(line) or _REF_BULLET_URL_RE.search(line)
        if not um:
            continue
        url = _strip_url_tail(um.group(1))
        if url.startswith("http"):
            out.append(UrlRef(url=url, location="refs"))
    return out


def _extract_block_urls(md_text: str) -> list[UrlRef]:
    """timeline / relations / chart / table ブロックの URL を抽出する。

    timeline は `url` フィールド、その他 3 種は `source` 内の生 URL を拾う。
    JSON パース失敗時はその block を skip (render 側が別途エラーにする)。
    """
    out: list[UrlRef] = []
    for m in _FENCED_RE.finditer(md_text):
        kind = m.group(1)
        try:
            data = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        if kind == "timeline":
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                u = str(item.get("url", "")).strip()
                if u.startswith("http"):
                    out.append(UrlRef(url=_strip_url_tail(u), location="timeline"))
        else:
            if not isinstance(data, dict):
                continue
            source = str(data.get("source", ""))
            for um in _INLINE_URL_RE.finditer(source):
                out.append(UrlRef(
                    url=_strip_url_tail(um.group(0)),
                    location=f"{kind}.source",
                ))
    return out


def extract_urls(md_text: str) -> list[UrlRef]:
    """DeepDive md から検証対象 URL を全件抽出する。重複は排除しない (location が違えば別件扱い)。"""
    return _extract_refs_urls(md_text) + _extract_block_urls(md_text)


def _probe(
    url: str,
    *,
    method: str,
    timeout: float,
    range_header: bool,
    ua: str = _UA,
) -> tuple[int | None, str]:
    """HEAD または GET (range) 1 回。(status, detail) を返す。

    `ua` を切り替えて Chrome / Safari の 2 段プローブを可能にする (anti-bot 剥がし用)。
    UA タグは detail に含めて、どの UA で何が返ったかを後段ログから追える形にする。
    """
    ua_tag = "ChromeWin" if "Windows" in ua else "SafariMac" if "Macintosh" in ua else "UA"
    # 一部 Apache 系 WAF は Accept-Language 等で fingerprint してくるが、curl 既定の
    # `Accept: */*` のみだと真の 404 を返すサイト (techxplore 等) がある。Apache 系
    # 真贋判定のためには Accept のみ最低限にする。
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
    }
    if range_header:
        # 4 KB だけ取得して帯域節約 (anti-bot HEAD 拒否サイトを GET で再判定する想定)。
        headers["Range"] = "bytes=0-4095"
    try:
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            import certifi
        except ModuleNotFoundError:
            context = ssl.create_default_context()
        else:
            context = ssl.create_default_context(cafile=certifi.where())
        with safe_urlopen(req, timeout=timeout, context=context) as resp:
            return int(resp.status), f"{method}[{ua_tag}] {resp.status}"
    except urllib.error.HTTPError as e:
        return int(e.code), f"{method}[{ua_tag}] {e.code}"
    except urllib.error.URLError as e:
        return None, f"{method}[{ua_tag}] URLError: {e.reason}"
    except (TimeoutError, ConnectionError) as e:
        return None, f"{method}[{ua_tag}] {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return None, f"{method}[{ua_tag}] {type(e).__name__}: {e}"


def _probe_system_tls(url: str, *, timeout: float) -> tuple[int | None, str]:
    """Python CAで失敗したURLだけをOSのTLS境界で再検証する。"""
    try:
        validated_url, host, port, addresses = resolve_public_http_endpoint(url)
    except ValueError as exc:
        return None, f"curl[SystemTLS] {exc}"
    detail = "no_public_address"
    for address in addresses:
        pinned = f"{host}:{port}:{f'[{address}]' if ':' in address else address}"
        result = proc.quiet_run(
            ["curl.exe", "-I", "--noproxy", "*", "--resolve", pinned, "--max-redirs", "0", "--max-time", str(max(1, int(timeout))),
             "--silent", "--show-error", "--output", os.devnull,
             "--write-out", "%{http_code}", validated_url],
            timeout=timeout + 2,
            check=False,
        )
        raw = (result.stdout or "").strip()
        match = re.search(r"(\d{3})$", raw)
        if match:
            status = int(match.group(1))
            return status, f"curl[SystemTLS] {status}"
        detail = (result.stderr or raw or f"exit={result.returncode}").strip()
    return None, f"curl[SystemTLS] {detail}"


def _verify_one(ref: UrlRef, *, timeout: float) -> UrlVerdict:
    """1 URL を多段プローブ (HEAD ChromeWin → GET ChromeWin → GET SafariMac) で判定する。

    判定方針 (2026-06-03 追加学習: TechXplore の捏造 URL が 1 段プローブの 403→403 を
    すり抜けて公開された反省から多段化):

    - 任意のプローブで **404 / 410 が観測されたら即 FATAL** (UA 偽装の裏にも見える 404 を露出させる)
    - 任意のプローブで 200-399 が返れば OK 確定
    - 全プローブで 403/405/501 のみ (一度も 200 にも 404 にもなれない) → anti-bot 継続として
      ambiguous OK (Bloomberg / theinformation 等)
    - DNS 解決失敗だけは 1 段目で FATAL に格上げ (捏造ホスト疑い)
    - その他のネットワークエラーが続いた場合は検証不能として FATAL
    """
    statuses: list[int | None] = []
    details: list[str] = []

    # 1 段目: HEAD ChromeWin (帯域節約)
    s1, d1 = _probe(ref.url, method="HEAD", timeout=timeout, range_header=False, ua=_UAS[0])
    statuses.append(s1); details.append(d1)
    if s1 is not None and 200 <= s1 < 400:
        return UrlVerdict(ref, s1, True, d1)
    if s1 in (404, 410):
        return UrlVerdict(ref, s1, False, d1)

    # DNS 失敗は即 fatal (ホスト存在せず → 捏造ホスト疑い)
    if "getaddrinfo" in d1 or "Name or service not known" in d1 or "NXDOMAIN" in d1.lower():
        return UrlVerdict(ref, None, False, f"{d1} (DNS 解決失敗 = 捏造ホスト疑い)")

    # 2 段目: GET range ChromeWin (HEAD 拒否のみ防御している鯖を剥がす)
    s2, d2 = _probe(ref.url, method="GET", timeout=timeout, range_header=True, ua=_UAS[0])
    statuses.append(s2); details.append(d2)
    if s2 is not None and 200 <= s2 < 400:
        return UrlVerdict(ref, s2, True, f"{d1} → {d2} (HEAD 拒否)")
    if s2 in (404, 410):
        return UrlVerdict(ref, s2, False, f"{d1} → {d2}")

    # 3 段目: GET range SafariMac (Chrome UA 拒否の Apache 系を剥がす。TechXplore はここで 404 を出す)
    s3, d3 = _probe(ref.url, method="GET", timeout=timeout, range_header=True, ua=_UAS[1])
    statuses.append(s3); details.append(d3)
    if s3 is not None and 200 <= s3 < 400:
        return UrlVerdict(ref, s3, True, f"{d1} → {d2} → {d3} (Safari UA 必須)")
    if s3 in (404, 410):
        return UrlVerdict(ref, s3, False, f"{d1} → {d2} → {d3}")

    if any("CERTIFICATE_VERIFY_FAILED" in detail for detail in details):
        system_status, system_detail = _probe_system_tls(ref.url, timeout=timeout)
        details.append(system_detail)
        if system_status is not None and 200 <= system_status < 400:
            return UrlVerdict(ref, system_status, True, " → ".join(details))
        if system_status in (403, 405, 429, 501):
            return UrlVerdict(ref, system_status, False, f"{' → '.join(details)} (anti-bot・ambiguous)")
        if system_status is not None:
            return UrlVerdict(ref, system_status, False, " → ".join(details))

    # ここまで来たら 3 プローブとも非 2xx 非 404。
    valid_codes = [s for s in statuses if s is not None]
    if valid_codes:
        # 403/405/429/501 系のみで終わるなら anti-bot 継続。ambiguous OK
        # (Bloomberg/theinformation 系 = 403/405、techxplore 系 = 429 レート制限)。
        # 429 は永続死リンクではなく一時的レート制限なので、404/410 が観測されない限り
        # 偽陽性 fatal を出さない (techxplore.com で実機 WebFetch で生存確認済の URL を
        # 死リンク扱いしないため・2026-06-05)。
        if all(s in (401, 403, 405, 406, 429, 501) for s in valid_codes):
            return UrlVerdict(ref, valid_codes[-1], False,
                              f"{' → '.join(details)} (anti-bot 全段継続・ambiguous)")
        # それ以外 (4xx の別 / 5xx) は fatal 寄せ
        return UrlVerdict(ref, valid_codes[-1], False, " → ".join(details))

    # URL構文やHTTP要求生成そのものの失敗は、通信障害ではなく入力欠陥なので fatal。
    request_errors = ("UnicodeEncodeError", "InvalidURL", "ValueError")
    if any(marker in detail for detail in details for marker in request_errors):
        return UrlVerdict(
            ref,
            None,
            False,
            f"{' / '.join(details)} (URLをHTTP要求へ変換できず検証不能)",
        )

    # オフライン許可は呼出し境界の明示skipだけ。検証不能を生存へ読み替えない。
    return UrlVerdict(ref, None, False,
                      f"{' / '.join(details)} (通信不能のためURL生存を検証不能)")


def verify_urls(
    refs: Iterable[UrlRef],
    *,
    timeout: float = 10.0,
    max_workers: int = 8,
) -> list[UrlVerdict]:
    """URL を並列に検証する。順序は入力順を保持。"""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 16:
        raise ValueError("url_probe_workers_out_of_policy")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)) or not 1.0 <= float(timeout) <= 30.0:
        raise ValueError("url_probe_timeout_out_of_policy")
    refs_list: list[UrlRef] = []
    for ref in refs:
        if len(refs_list) >= 1024:
            raise ValueError("url_probe_reference_budget_exceeded")
        refs_list.append(ref)
    if not refs_list:
        return []

    unique_refs: list[UrlRef] = []
    seen_urls: set[str] = set()
    for ref in refs_list:
        if ref.url in seen_urls:
            continue
        seen_urls.add(ref.url)
        unique_refs.append(ref)
        if len(unique_refs) > 256:
            raise ValueError("url_probe_unique_budget_exceeded")

    results: dict[int, UrlVerdict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_to_idx = {ex.submit(_verify_one, r, timeout=timeout): i for i, r in enumerate(unique_refs)}
        for fut in as_completed(fut_to_idx):
            idx = fut_to_idx[fut]
            results[idx] = fut.result()
    by_url = {results[i].ref.url: results[i] for i in range(len(unique_refs))}
    return [
        UrlVerdict(ref, by_url[ref.url].status, by_url[ref.url].ok, by_url[ref.url].detail)
        for ref in refs_list
    ]


def require_live_urls(md_path: Path, md_text: str) -> list[UrlVerdict]:
    """md 内の全 URL を検証し、fatal が 1 件でもあれば DeepDiveUrlError を raise する。

    `NEWS_GRASP_SKIP_URL_CHECK=1` 環境変数で全スキップ可能 (CI/オフライン用)。
    本番 runner は環境変数を立てないので常時 ON のまま。
    """
    if os.environ.get("NEWS_GRASP_SKIP_URL_CHECK") == "1":
        return []
    refs = extract_urls(md_text)
    verdicts = verify_urls(refs)
    fatal = [v for v in verdicts if not v.ok]
    if fatal:
        lines = [
            f"{md_path.name}: {len(fatal)}/{len(verdicts)} 件の URL が生存検証 NG。",
            "捏造 URL または恒久 404 を含む記事は公開しない (2026-06-03 三菱UFJ FX_Monthly 事故の恒久対策)。",
        ]
        for v in fatal:
            lines.append(f"  [{v.ref.location}] {v.detail}  {v.ref.url}")
        raise DeepDiveUrlError("\n".join(lines))
    return verdicts


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    args = argv[1:]
    all_mode = False
    paths: list[Path] = []
    for a in args:
        if a == "--all":
            all_mode = True
        elif a.startswith("-"):
            print(f"unknown flag: {a}", file=sys.stderr)
            return 2
        else:
            paths.append(Path(a))

    if all_mode:
        src_dir = _PKG_ROOT / "digest" / "DeepDive"
        paths = sorted(src_dir.glob("*.md"))

    if not paths:
        print("usage: python -m tools.validate_deepdive_urls [--all] [path/to/md ...]", file=sys.stderr)
        return 2

    failed = 0
    for p in paths:
        md = p.read_text(encoding="utf-8")
        try:
            verdicts = require_live_urls(p, md)
            n_ok = sum(1 for v in verdicts if v.ok)
            print(f"OK  {p.name}: {n_ok}/{len(verdicts)} URL 生存")
            for v in verdicts:
                if "ambiguous" in v.detail or "anti-bot" in v.detail:
                    print(f"     [{v.ref.location}] {v.detail}  {v.ref.url}")
        except DeepDiveUrlError as e:
            failed += 1
            print(f"NG  {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
