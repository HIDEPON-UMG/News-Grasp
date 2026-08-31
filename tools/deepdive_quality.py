"""DeepDive URL provenanceと対談価値を一つの境界で検証する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import ssl
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from tools.deepdive_content import contains_internal_metadata
from tools.tts import deepdive_dialogue, proc
from tools.validate_deepdive_urls import extract_urls


LEGACY_SCHEMA = "DEEPDIVE_SOURCE_PROVENANCE_V1"
SCHEMA = "DEEPDIVE_SOURCE_PROVENANCE_V2"
REPORT_SCHEMA = "DEEPDIVE_SHARED_QUALITY_REPORT_V1"
HISTORY_REPORT_SCHEMA = "DEEPDIVE_SHARED_QUALITY_HISTORY_REPORT_V1"
HISTORY_OUTPUT_PREFIX = Path("data/deepdive-history-remediation")
BUNDLE_SCHEMA = "DEEPDIVE_ISSUE_BUNDLE_V1"
CLAIM_SOURCE_TRANSPORT_SCHEMA = "DEEPDIVE_CLAIM_SOURCE_TRANSPORT_V1"
DEEPDIVE_QUALITY_REVIEW_V2 = "DEEPDIVE_QUALITY_REVIEW_V2"
DEEPDIVE_SHARED_QUALITY_ROUTES_SCHEMA = "DEEPDIVE_SHARED_QUALITY_ROUTES_V2"
DEEPDIVE_SHARED_QUALITY_ROUTES_PATH = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "deepdive_quality_routes.json"
)
DEEPDIVE_SHARED_QUALITY_ISSUE_CODES = frozenset(
    {
        "deepdive_url_provenance_invalid",
        "deepdive_article_value_invalid",
        "deepdive_relation_quality_invalid",
        "deepdive_dialogue_value_invalid",
        "deepdive_research_evidence_insufficient",
        "deepdive_public_surface_invalid",
    }
)
DEEPDIVE_QUALITY_REVIEW_AXES = (
    "theme_specific_insight",
    "evidence_depth",
    "causal_coherence",
    "counterevidence",
    "decision_utility",
    "dialogue_naturalness",
    "relation_map_utility",
)
DEEPDIVE_QUALITY_REVIEW_ROUTES = frozenset(
    {
        "production_generation",
        "repair_publish",
        "daily_quality",
        "codex_daily_audit",
    }
)
DEEPDIVE_QUALITY_REVIEW_ARTIFACTS = ("article", "relation", "dialogue")
DEEPDIVE_QUALITY_REVIEW_ISSUE_CODES = {
    "article": "deepdive_article_value_invalid",
    "relation": "deepdive_relation_quality_invalid",
    "dialogue": "deepdive_dialogue_value_invalid",
}
DEEPDIVE_QUALITY_REVIEW_ARTICLE_AXES = frozenset(
    {
        "theme_specific_insight",
        "evidence_depth",
        "causal_coherence",
        "counterevidence",
        "decision_utility",
    }
)
NG_RC_01_DEEPDIVE_SYSTEM_TRANSPORT_FALLBACK = (
    "NG_RC_01_DEEPDIVE_SYSTEM_TRANSPORT_FALLBACK"
)
NG_RC_02_DEEPDIVE_ISSUE_BUNDLE = "NG_RC_02_DEEPDIVE_ISSUE_BUNDLE"
MAX_AUDIT_PERIOD_DAYS = 31
OBSERVATION_CACHE_SCHEMA = "DEEPDIVE_URL_OBSERVATION_CACHE_V1"
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
ISSUE_DATE_RE = re.compile(r"^(20\d{2}-\d{2}-\d{2})-DeepDive\.md$")
CLAIM_SOURCE_ENFORCEMENT_DATE = date(2026, 8, 25)
CLAIM_SOURCE_RE = re.compile(
    r"<!--\s*claim-source:\s*(\{.*?\})\s*-->",
    re.DOTALL,
)
CLAIM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_OBSERVED_BYTES = 4 * 1024 * 1024
MAX_CLAIM_EVIDENCE_BYTES = 512 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
SOFT_404_PATTERNS = (
    re.compile(rb"<title[^>]*>[^<]*(?:404|not[ -]?found|page not found)[^<]*</title>", re.I),
    re.compile(rb"(?:the page|page) you requested (?:could not be found|was not found)", re.I),
)


class DeepDiveQualityError(RuntimeError):
    """DeepDive品質をGreenとして表現できない。"""


class _RenderedHrefCollector(HTMLParser):
    """生成HTMLの実anchor hrefだけを収集する。本文文字列は証拠にしない。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.add(value)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_text_sha256(payload: bytes) -> str:
    text = payload.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _portable_article_path(path: Path) -> str:
    article = Path(path).resolve()
    if article.parent.name == "DeepDive" and article.parent.parent.name == "digest":
        return f"digest/DeepDive/{article.name}"
    return article.name


def _audit_file_evidence(path: Path, payload: bytes) -> dict[str, str]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "canonicalTextSha256": _canonical_text_sha256(payload),
    }


def canonical_manifest_sha256(value: dict[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("manifestSha256", None)
    return _canonical_sha256(unsigned)


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeepDiveQualityError(code) from error
    if not isinstance(value, dict):
        raise DeepDiveQualityError(code)
    return value


def load_shared_quality_route_registry(
    registry_path: Path | str | None = None,
    *,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """共有DeepDive quality registryを読み取り専用で検証して返す。"""

    if registry_path is not None:
        candidate = Path(registry_path)
        path = (
            candidate.resolve() / "config" / "deepdive_quality_routes.json"
            if candidate.is_dir()
            else candidate
        )
    elif repo_root is not None:
        path = Path(repo_root).resolve() / "config" / "deepdive_quality_routes.json"
    else:
        path = DEEPDIVE_SHARED_QUALITY_ROUTES_PATH
    invalid_prefix = "DEEPDIVE_SHARED_QUALITY_ROUTES_INVALID"
    try:
        if not path.is_file() or path.is_symlink():
            raise OSError("registry missing")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeepDiveQualityError(f"{invalid_prefix} unreadable") from error
    if not isinstance(value, dict):
        raise DeepDiveQualityError(f"{invalid_prefix} schema")

    expected_keys = {
        "schemaVersion",
        "engine",
        "issueCodes",
        "declaredRoutes",
        "consumerRoutes",
        "positiveFixtureRoutes",
        "negativeFixtureRoutes",
        "unknownRoutePolicy",
    }
    if set(value) != expected_keys:
        raise DeepDiveQualityError(f"{invalid_prefix} keys")
    if value.get("schemaVersion") != DEEPDIVE_SHARED_QUALITY_ROUTES_SCHEMA:
        raise DeepDiveQualityError(f"{invalid_prefix} schemaVersion")
    if value.get("engine") != "tools.deepdive_quality":
        raise DeepDiveQualityError(f"{invalid_prefix} engine")

    issue_codes = value.get("issueCodes")
    if (
        not isinstance(issue_codes, list)
        or not all(isinstance(item, str) for item in issue_codes)
        or len(issue_codes) != len(set(issue_codes))
        or set(issue_codes) != DEEPDIVE_SHARED_QUALITY_ISSUE_CODES
    ):
        raise DeepDiveQualityError(f"{invalid_prefix} issueCodes")

    for field in (
        "declaredRoutes",
        "consumerRoutes",
        "positiveFixtureRoutes",
        "negativeFixtureRoutes",
    ):
        routes = value.get(field)
        if (
            not isinstance(routes, list)
            or not all(isinstance(item, str) for item in routes)
            or len(routes) != len(set(routes))
            or set(routes) != DEEPDIVE_QUALITY_REVIEW_ROUTES
        ):
            raise DeepDiveQualityError(f"{invalid_prefix} {field}")
    if value.get("unknownRoutePolicy") != "fail_closed":
        raise DeepDiveQualityError(f"{invalid_prefix} unknownRoutePolicy")

    return value


# 既存の呼出し名は同一loaderへの互換aliasとし、検証ownerを一つに保つ。
load_shared_quality_routes = load_shared_quality_route_registry


def _validate_shared_quality_route_registry(route: str) -> None:
    """module側の共有route registryと呼出しrouteを監査前に確定する。"""

    value = load_shared_quality_route_registry()
    if route not in value["declaredRoutes"]:
        raise DeepDiveQualityError(
            f"DEEPDIVE_SHARED_QUALITY_ROUTE_UNKNOWN {route}"
        )


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_text(path: Path, value: str) -> None:
    """同一directoryでUTF-8 textをflush後に置換する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """同一directoryでbytesをflush後に置換する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _exclusive_stage_file(target: Path, *, repo_root: Path) -> Path:
    """同一safe parent内に予測不能かつ排他的なstage leafを予約する。"""

    descriptor, stage_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".stage", dir=target.parent
    )
    os.close(descriptor)
    return _safe_repo_path(
        Path(stage_name), repo_root=repo_root, file_required=True
    )


def _safe_repo_path(
    path: Path,
    *,
    repo_root: Path,
    file_required: bool = False,
) -> Path:
    """materializerのread/write pathを非reparseなrepo subtreeへ固定する。"""

    boundary = Path(repo_root).resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    if boundary != candidate and boundary not in candidate.parents:
        raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
    cursor = candidate
    while True:
        if cursor.exists():
            metadata = os.lstat(cursor)
            if cursor.is_symlink() or int(
                getattr(metadata, "st_file_attributes", 0)
            ) & 0x400:
                raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
        if os.path.normcase(str(cursor)) == os.path.normcase(str(boundary)):
            break
        parent = cursor.parent
        if parent == cursor:
            raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
        cursor = parent
    resolved = candidate.resolve(strict=False)
    if boundary != resolved and boundary not in resolved.parents:
        raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
    if file_required and (not candidate.is_file() or candidate.is_symlink()):
        raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
    return candidate


def _snapshot_materializer_file(
    path: Path,
    *,
    repo_root: Path,
) -> bytes | None:
    """安全な最終pathの開始時bytesを取得する。"""

    candidate = _safe_repo_path(path, repo_root=repo_root)
    if not candidate.exists():
        return None
    if candidate.is_symlink() or not candidate.is_file():
        raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
    try:
        return candidate.read_bytes()
    except OSError as error:
        raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID") from error


def _snapshot_rendered_files(
    directory: Path,
    *,
    repo_root: Path,
) -> dict[Path, bytes]:
    """指定issueの公開directory内にあるregular fileだけを記録する。"""

    root = _safe_repo_path(directory, repo_root=repo_root)
    if not root.exists():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
    snapshots: dict[Path, bytes] = {}
    for current, dirnames, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current_path = Path(current)
        for dirname in dirnames:
            child = current_path / dirname
            _safe_repo_path(child, repo_root=repo_root)
            if child.is_symlink() or not child.is_dir():
                raise DeepDiveQualityError("DEEPDIVE_MATERIALIZER_PATH_INVALID")
        for filename in filenames:
            child = current_path / filename
            child = _safe_repo_path(
                child,
                repo_root=repo_root,
                file_required=True,
            )
            try:
                snapshots[child] = child.read_bytes()
            except OSError as error:
                raise DeepDiveQualityError(
                    "DEEPDIVE_MATERIALIZER_PATH_INVALID"
                ) from error
    return snapshots


def _restore_materializer_file(
    path: Path,
    payload: bytes | None,
    *,
    repo_root: Path,
) -> None:
    """開始時snapshotへ戻す。危険なsymlink/reparseは変更しない。"""

    try:
        candidate = _safe_repo_path(path, repo_root=repo_root)
    except DeepDiveQualityError:
        return
    if candidate.exists() and (
        candidate.is_symlink() or not candidate.is_file()
    ):
        return
    if payload is None:
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                return
        return
    try:
        _atomic_write_bytes(candidate, payload)
    except (DeepDiveQualityError, OSError):
        return


def _restore_rendered_files(
    directory: Path,
    snapshots: dict[Path, bytes],
    *,
    repo_root: Path,
) -> None:
    """公開issue directoryだけを開始時snapshotへ戻す。"""

    try:
        current = _snapshot_rendered_files(directory, repo_root=repo_root)
    except DeepDiveQualityError:
        return
    for path in sorted(current):
        if path not in snapshots:
            _restore_materializer_file(path, None, repo_root=repo_root)
    for path, payload in snapshots.items():
        _restore_materializer_file(path, payload, repo_root=repo_root)


def _load_observation_cache(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    value = _read_json(path, "DEEPDIVE_OBSERVATION_CACHE_INVALID")
    observations = value.get("observations")
    unsigned = dict(value)
    unsigned.pop("cacheSha256", None)
    if (
        value.get("schemaVersion") != OBSERVATION_CACHE_SCHEMA
        or not isinstance(observations, dict)
        or value.get("cacheSha256") != _canonical_sha256(unsigned)
    ):
        raise DeepDiveQualityError("DEEPDIVE_OBSERVATION_CACHE_INVALID")
    normalized: dict[str, dict[str, object]] = {}
    for url, record in observations.items():
        normalized[url] = _normalize_fetch_records(
            [record], expected_urls={url}
        )[url]
    return normalized


def _write_observation_cache(
    path: Path,
    observations: dict[str, dict[str, object]],
) -> None:
    cache_fields = ("url", "finalUrl", "httpStatus", "fetchedAt", "contentSha256")
    value: dict[str, Any] = {
        "schemaVersion": OBSERVATION_CACHE_SCHEMA,
        "status": "Green",
        "observations": {
            url: {
                **{
                    field: observations[url][field]
                    for field in cache_fields
                },
                **(
                    {"transportEvidence": observations[url]["transportEvidence"]}
                    if "transportEvidence" in observations[url]
                    else {}
                ),
            }
            for url in sorted(observations)
        },
    }
    value["cacheSha256"] = _canonical_sha256(value)
    _atomic_write_json(path, value)


def _seed_observations_from_manifests(
    *,
    repo: Path,
    articles: list[Path],
) -> dict[str, dict[str, object]]:
    observed: dict[str, dict[str, object]] = {}
    for article in articles:
        issue_date = _issue_date(article)
        manifest = repo / "data" / "deepdive-provenance" / f"{issue_date}.json"
        if validate_provenance(article, manifest):
            continue
        value = _read_json(manifest, "DEEPDIVE_PROVENANCE_INVALID")
        for row in value["sources"]:
            url = row["url"]
            observed[url] = {
                "url": url,
                "finalUrl": row["finalUrl"],
                "httpStatus": row["httpStatus"],
                "fetchedAt": row["fetchedAt"],
                "contentSha256": row["contentSha256"],
                **(
                    {"transportEvidence": row["transportEvidence"]}
                    if "transportEvidence" in row
                    else {}
                ),
            }
    return observed


def _issue_date(article_path: Path) -> str:
    match = ISSUE_DATE_RE.fullmatch(article_path.name)
    if not match:
        raise DeepDiveQualityError("DEEPDIVE_ARTICLE_NAME_INVALID")
    return match.group(1)


def _article_url_locations(article_text: str) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for ref in extract_urls(article_text):
        result.setdefault(ref.url, set()).add(ref.location)
    return {url: sorted(locations) for url, locations in sorted(result.items())}


def _normalized_evidence_text(value: str) -> str:
    """取得本文と短い根拠spanをmarkup差に左右されず照合する。"""

    without_markup = re.sub(r"<[^>]+>", " ", unescape(value))
    return re.sub(r"\s+", " ", without_markup).strip().casefold()


_GENERIC_EVIDENCE_RE = re.compile(
    r"^(?:(?:元記事|記事(?:本文)?|一次(?:資料|ソース)|出典)"
    r"(?:の内容)?(?:を)?(?:確認|参照)(?:する|した|済み)?"
    r"|(?:source|official source|see source|check source))"
    r"[。.!！?？]*$",
    re.IGNORECASE,
)


def _claim_article_value_issues(
    declarations: list[dict[str, str]],
) -> list[str]:
    """claim本文の複製や汎用文だけのevidenceを記事価値Redに分類する。"""

    issues: list[str] = []
    for row in declarations:
        claim = _normalized_evidence_text(row["claim"])
        evidence = _normalized_evidence_text(row["evidence"])
        if evidence == claim:
            issues.append(
                "DEEPDIVE_ARTICLE_VALUE_INVALID "
                f"claim={row['claimId']} evidence_duplicate"
            )
        elif _GENERIC_EVIDENCE_RE.fullmatch(evidence):
            issues.append(
                "DEEPDIVE_ARTICLE_VALUE_INVALID "
                f"claim={row['claimId']} evidence_generic"
            )
    return issues


def _claim_source_declarations(article_text: str) -> list[dict[str, str]]:
    declarations: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for match in CLAIM_SOURCE_RE.finditer(article_text):
        try:
            raw = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise DeepDiveQualityError(
                "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID malformed_binding"
            ) from error
        if not isinstance(raw, dict) or set(raw) != {
            "claimId",
            "claim",
            "sourceUrl",
            "evidence",
        }:
            raise DeepDiveQualityError(
                "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID binding_schema"
            )
        row = {key: str(raw[key]).strip() for key in raw}
        claim_id = row["claimId"]
        if (
            CLAIM_ID_RE.fullmatch(claim_id) is None
            or claim_id in seen_ids
            or not row["claim"]
            or not row["sourceUrl"].startswith(("http://", "https://"))
        ):
            raise DeepDiveQualityError(
                "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID binding_value"
            )
        if len(_normalized_evidence_text(row["evidence"])) < 12:
            raise DeepDiveQualityError(
                "DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT evidence_short"
            )
        seen_ids.add(claim_id)
        declarations.append(row)
    return declarations


def _claim_free_article_sha256(article_text: str) -> str:
    """claim commentを除いた記事同一性を改行・空白差に依存せず固定する。"""

    without_comments = CLAIM_SOURCE_RE.sub("", article_text)
    canonical = re.sub(r"\s+", " ", without_comments).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim_source_transport_value(
    *, article_text: str, article_path: Path, issue_date: str
) -> dict[str, Any]:
    bindings = sorted(
        _claim_source_declarations(article_text), key=lambda row: row["claimId"]
    )
    if not bindings:
        raise DeepDiveQualityError("DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID bindings")
    value: dict[str, Any] = {
        "schemaVersion": CLAIM_SOURCE_TRANSPORT_SCHEMA,
        "status": "Green",
        "issueDate": issue_date,
        "articlePath": _portable_article_path(article_path),
        "articleContentSha256": _claim_free_article_sha256(article_text),
        "bindings": bindings,
    }
    value["transportSha256"] = _canonical_sha256(value)
    return value


def _load_claim_source_transport(
    *, path: Path, article_text: str, article_path: Path, issue_date: str
) -> list[dict[str, str]] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise DeepDiveQualityError("DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID path")
    value = _read_json(path, "DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID json")
    unsigned = dict(value)
    observed_seal = unsigned.pop("transportSha256", None)
    article_content_sha = value.get("articleContentSha256")
    if (
        set(value)
        != {
            "schemaVersion",
            "status",
            "issueDate",
            "articlePath",
            "articleContentSha256",
            "bindings",
            "transportSha256",
        }
        or value.get("schemaVersion") != CLAIM_SOURCE_TRANSPORT_SCHEMA
        or value.get("status") != "Green"
        or value.get("issueDate") != issue_date
        or value.get("articlePath") != _portable_article_path(article_path)
        or HEX_64_RE.fullmatch(str(article_content_sha or "")) is None
        or observed_seal != _canonical_sha256(unsigned)
        or not isinstance(value.get("bindings"), list)
    ):
        raise DeepDiveQualityError("DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID seal")
    serialized = "\n".join(
        f"<!-- claim-source: {json.dumps(row, ensure_ascii=False, separators=(',', ':'))} -->"
        for row in value["bindings"]
    )
    bindings = _claim_source_declarations(serialized)
    if len(bindings) != len(value["bindings"]):
        raise DeepDiveQualityError("DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID bindings")
    if article_content_sha != _claim_free_article_sha256(article_text):
        current_bindings = _claim_source_declarations(article_text)
        if not current_bindings:
            raise DeepDiveQualityError("DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID seal")
        return sorted(current_bindings, key=lambda row: row["claimId"])
    return bindings


def _restore_claim_sources_from_transport(
    article_text: str, *, bindings: list[dict[str, str]]
) -> str:
    """sealed transportの未欠落rowだけをfrontmatter直後へ決定論的に戻す。"""

    existing = _claim_source_declarations(article_text)
    transport_by_id = {row["claimId"]: row for row in bindings}
    for row in existing:
        if transport_by_id.get(row["claimId"]) != row:
            raise DeepDiveQualityError(
                "DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID existing_binding"
            )
    missing = [row for row in bindings if row["claimId"] not in {item["claimId"] for item in existing}]
    if not missing:
        return article_text
    comments = [
        f"<!-- claim-source: {json.dumps(row, ensure_ascii=False, separators=(',', ':'))} -->"
        for row in missing
    ]
    lines = article_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    insertion = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                insertion = index + 1
                break
    output = lines[:insertion] + comments + lines[insertion:]
    return "\n".join(output).rstrip() + "\n"


def _claim_binding_fingerprint(row: dict[str, str]) -> dict[str, str]:
    return {
        "claimId": row["claimId"],
        "claimSha256": hashlib.sha256(row["claim"].encode("utf-8")).hexdigest(),
        "sourceUrl": row["sourceUrl"],
        "evidenceSha256": hashlib.sha256(
            _normalized_evidence_text(row["evidence"]).encode("utf-8")
        ).hexdigest(),
    }


def _build_claim_bindings(
    *,
    article_text: str,
    issue_date: str,
    observed: dict[str, dict[str, object]],
) -> list[dict[str, str]]:
    declarations = _claim_source_declarations(article_text)
    if date.fromisoformat(issue_date) >= CLAIM_SOURCE_ENFORCEMENT_DATE and not declarations:
        raise DeepDiveQualityError(
            "DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT bindings_missing"
        )
    bindings: list[dict[str, str]] = []
    for row in declarations:
        record = observed.get(row["sourceUrl"])
        observed_text = str((record or {}).get("observedText") or "")
        evidence = _normalized_evidence_text(row["evidence"])
        if not record or not observed_text or evidence not in _normalized_evidence_text(observed_text):
            raise DeepDiveQualityError(
                "DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT "
                f"claim={row['claimId']} source={row['sourceUrl']}"
            )
        bindings.append(_claim_binding_fingerprint(row))
    return sorted(bindings, key=lambda item: item["claimId"])


def _normalize_fetch_records(
    records: list[dict[str, object]],
    *,
    expected_urls: set[str],
) -> dict[str, dict[str, object]]:
    if not isinstance(records, list):
        raise DeepDiveQualityError("DEEPDIVE_FETCH_RECORD_INVALID")
    normalized: dict[str, dict[str, object]] = {}
    required = {"url", "finalUrl", "httpStatus", "fetchedAt", "contentSha256"}
    optional = {"observedText", "transportEvidence"}
    for row in records:
        if (
            not isinstance(row, dict)
            or not required.issubset(row)
            or set(row) - required - optional
            or ("observedText" in row and not isinstance(row["observedText"], str))
        ):
            raise DeepDiveQualityError("DEEPDIVE_FETCH_RECORD_INVALID")
        transport = row.get("transportEvidence")
        if transport is not None and (
            not isinstance(transport, dict)
            or set(transport) != {
                "selectedTransport",
                "primaryTransport",
                "primaryFailure",
                "fallbackAttemptCount",
            }
            or transport.get("selectedTransport")
            not in {"python_urllib", "windows_system_http"}
            or transport.get("primaryTransport") != "python_urllib"
            or not isinstance(transport.get("primaryFailure"), (str, type(None)))
            or transport.get("fallbackAttemptCount") not in {0, 1}
            or (
                transport.get("selectedTransport") == "python_urllib"
                and (
                    transport.get("fallbackAttemptCount") != 0
                    or transport.get("primaryFailure") is not None
                )
            )
            or (
                transport.get("selectedTransport") == "windows_system_http"
                and (
                    transport.get("fallbackAttemptCount") != 1
                    or not str(transport.get("primaryFailure") or "")
                )
            )
        ):
            raise DeepDiveQualityError("DEEPDIVE_FETCH_TRANSPORT_EVIDENCE_INVALID")
        url = str(row["url"])
        status = row["httpStatus"]
        content_hash = str(row["contentSha256"]).casefold()
        if (
            url in normalized
            or url not in expected_urls
            or not str(row["finalUrl"]).startswith(("http://", "https://"))
            or not isinstance(status, int)
            or not 200 <= status < 400
            or not str(row["fetchedAt"]).strip()
            or HEX_64_RE.fullmatch(content_hash) is None
        ):
            raise DeepDiveQualityError("DEEPDIVE_FETCH_RECORD_INVALID")
        normalized[url] = {
            **row,
            "contentSha256": content_hash,
        }
    missing = expected_urls - set(normalized)
    if missing:
        raise DeepDiveQualityError(
            "DEEPDIVE_URL_NOT_OBSERVED " + ",".join(sorted(missing))
        )
    if set(normalized) != expected_urls:
        raise DeepDiveQualityError("DEEPDIVE_FETCH_RECORD_UNBOUND")
    return normalized


def build_provenance_manifest(
    *,
    article_path: Path,
    fetch_records: list[dict[str, object]],
    output_path: Path,
    article_bytes_override: bytes | None = None,
) -> dict[str, Any]:
    """実取得記録を記事内URLの全出現位置へ束縛する。"""

    try:
        article = Path(article_path).resolve(strict=True)
    except OSError as error:
        raise DeepDiveQualityError("DEEPDIVE_ARTICLE_MISSING") from error
    try:
        article_bytes = (
            article.read_bytes()
            if article_bytes_override is None
            else bytes(article_bytes_override)
        )
        text = article_bytes.decode("utf-8-sig")
    except (OSError, TypeError, UnicodeError) as error:
        raise DeepDiveQualityError("DEEPDIVE_ARTICLE_MISSING") from error
    issue_date = _issue_date(article)
    locations = _article_url_locations(text)
    if not locations:
        raise DeepDiveQualityError("DEEPDIVE_URL_SET_EMPTY")
    observed = _normalize_fetch_records(
        fetch_records,
        expected_urls=set(locations),
    )
    sources = [
        {
            "url": url,
            "publicHref": url,
            "finalUrl": observed[url]["finalUrl"],
            "httpStatus": observed[url]["httpStatus"],
            "fetchedAt": observed[url]["fetchedAt"],
            "contentSha256": observed[url]["contentSha256"],
            **(
                {"transportEvidence": observed[url]["transportEvidence"]}
                if "transportEvidence" in observed[url]
                else {}
            ),
            "locations": locations[url],
        }
        for url in sorted(locations)
    ]
    claim_bindings = _build_claim_bindings(
        article_text=text,
        issue_date=issue_date,
        observed=observed,
    )
    schema = (
        SCHEMA
        if date.fromisoformat(issue_date) >= CLAIM_SOURCE_ENFORCEMENT_DATE
        or claim_bindings
        else LEGACY_SCHEMA
    )
    value: dict[str, Any] = {
        "schemaVersion": schema,
        "status": "Green",
        "issueDate": issue_date,
        "articlePath": _portable_article_path(article),
        "articleSha256": _canonical_text_sha256(article_bytes),
        "sources": sources,
        "sourceSetSha256": _canonical_sha256(sources),
    }
    if schema == SCHEMA:
        value.update(
            {
                "claimBindings": claim_bindings,
                "claimSetSha256": _canonical_sha256(claim_bindings),
            }
        )
    if all("transportEvidence" in observed[url] for url in locations):
        value["transportEvidenceVersion"] = 1
    value["manifestSha256"] = canonical_manifest_sha256(value)
    _atomic_write_json(Path(output_path).resolve(), value)
    return value


def validate_provenance(
    article_path: Path,
    manifest_path: Path,
) -> list[str]:
    """manifestの自己hash、記事hash、URL位置、公開hrefを再読込する。"""

    issues, _evidence = _validate_provenance_with_evidence(
        article_path,
        manifest_path,
    )
    return issues


def validate_rendered_public_surface(
    manifest_path: Path,
    rendered_path: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """provenanceの公開URLが生成HTMLのanchor hrefに全件存在するか検証する。"""

    manifest = Path(manifest_path).resolve()
    rendered = Path(rendered_path).resolve()
    if not manifest.is_file():
        return ["DEEPDIVE_PROVENANCE_MISSING"], []
    if not rendered.is_file():
        return ["DEEPDIVE_RENDERED_HTML_MISSING"], []
    try:
        value = json.loads(manifest.read_text(encoding="utf-8-sig"))
        rendered_bytes = rendered.read_bytes()
        rendered_text = rendered_bytes.decode("utf-8-sig")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["DEEPDIVE_RENDERED_PUBLIC_SURFACE_INVALID"], []
    sources = value.get("sources") if isinstance(value, dict) else None
    if not isinstance(sources, list):
        return ["DEEPDIVE_RENDERED_PUBLIC_MANIFEST_INVALID"], []
    required_hrefs: set[str] = set()
    for row in sources:
        if not isinstance(row, dict):
            return ["DEEPDIVE_RENDERED_PUBLIC_MANIFEST_INVALID"], []
        href = row.get("publicHref")
        if not isinstance(href, str) or not href.startswith(("http://", "https://")):
            return ["DEEPDIVE_RENDERED_PUBLIC_MANIFEST_INVALID"], []
        required_hrefs.add(href)
    collector = _RenderedHrefCollector()
    try:
        collector.feed(rendered_text)
        collector.close()
    except (ValueError, TypeError):
        return ["DEEPDIVE_RENDERED_PUBLIC_SURFACE_INVALID"], []
    issues: list[str] = []
    if contains_internal_metadata(rendered_text):
        issues.append("DEEPDIVE_PUBLIC_METADATA_EXPOSED")
    issues.extend(
        f"DEEPDIVE_RENDERED_PUBLIC_HREF_MISSING {href}"
        for href in sorted(required_hrefs - collector.hrefs)
    )
    if value.get("schemaVersion") == SCHEMA:
        source_sha = str(value.get("articleSha256") or "")
        source_marker = re.search(
            r'<meta\s+name=["\']news-grasp-source-sha256["\']\s+'
            r'content=["\']([a-f0-9]{64})["\']\s*/?>',
            rendered_text,
            re.IGNORECASE,
        )
        if source_marker is None:
            issues.append("DEEPDIVE_RENDERED_SOURCE_SHA_MISSING")
        elif source_marker.group(1).casefold() != source_sha.casefold():
            issues.append("DEEPDIVE_RENDERED_SOURCE_DRIFT")
    return issues, [_audit_file_evidence(rendered, rendered_bytes)]


def validate_claim_source_fit(
    article_path: Path,
    manifest_path: Path,
    *,
    article_text_override: str | None = None,
    require_v2_claim_sources: bool = False,
) -> list[str]:
    """生成時に実取得本文へ照合したclaim bindingを記事とmanifestへ再束縛する。"""

    article = Path(article_path).resolve()
    manifest = Path(manifest_path).resolve()
    if not article.is_file() or not manifest.is_file():
        return []
    try:
        article_text = (
            article.read_text(encoding="utf-8-sig")
            if article_text_override is None
            else article_text_override
        )
        value = json.loads(manifest.read_text(encoding="utf-8-sig"))
        issue_day = date.fromisoformat(_issue_date(article))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return ["DEEPDIVE_CLAIM_SOURCE_FIT_INVALID"]
    if not isinstance(value, dict):
        return ["DEEPDIVE_CLAIM_SOURCE_FIT_INVALID"]
    schema = value.get("schemaVersion")
    if schema == LEGACY_SCHEMA:
        if not (require_v2_claim_sources or issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE):
            return []
        issues = ["DEEPDIVE_CLAIM_SOURCE_MANIFEST_LEGACY"]
        if require_v2_claim_sources:
            issues.append("DEEPDIVE_CLAIM_SOURCE_BINDINGS_MISSING")
        return issues
    if schema != SCHEMA:
        return ["DEEPDIVE_CLAIM_SOURCE_SCHEMA_INVALID"]
    try:
        declarations = _claim_source_declarations(article_text)
        expected = sorted(
            (_claim_binding_fingerprint(row) for row in declarations),
            key=lambda item: item["claimId"],
        )
    except DeepDiveQualityError as error:
        return [str(error)]
    if (
        require_v2_claim_sources or issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE
    ) and not expected:
        return ["DEEPDIVE_CLAIM_SOURCE_BINDINGS_MISSING"]
    actual = value.get("claimBindings")
    issues: list[str] = _claim_article_value_issues(declarations)
    if actual != expected:
        issues.append("DEEPDIVE_CLAIM_SOURCE_BINDING_DRIFT")
    if value.get("claimSetSha256") != _canonical_sha256(actual):
        issues.append("DEEPDIVE_CLAIM_SOURCE_SET_DRIFT")
    source_urls = {
        str(row.get("url"))
        for row in value.get("sources", [])
        if isinstance(row, dict)
    }
    if any(row["sourceUrl"] not in source_urls for row in expected):
        issues.append("DEEPDIVE_CLAIM_SOURCE_URL_UNBOUND")
    return sorted(set(issues))


def _validate_provenance_with_evidence(
    article_path: Path,
    manifest_path: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """同じ読取bytesでprovenance判定とSHA-256証跡を生成する。"""

    issues: list[str] = []
    evidence: list[dict[str, str]] = []
    article = Path(article_path).resolve()
    manifest = Path(manifest_path).resolve()
    if not article.is_file():
        return ["DEEPDIVE_ARTICLE_MISSING"], evidence
    if not manifest.is_file():
        return ["DEEPDIVE_PROVENANCE_MISSING"], evidence
    try:
        article_bytes = article.read_bytes()
        manifest_bytes = manifest.read_bytes()
        article_text = article_bytes.decode("utf-8-sig")
        value = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["DEEPDIVE_PROVENANCE_INVALID"], evidence
    if not isinstance(value, dict):
        return ["DEEPDIVE_PROVENANCE_INVALID"], evidence
    evidence.extend(
        (
            _audit_file_evidence(article, article_bytes),
            _audit_file_evidence(manifest, manifest_bytes),
        )
    )
    required_fields = {
        "schemaVersion",
        "status",
        "issueDate",
        "articlePath",
        "articleSha256",
        "sources",
        "sourceSetSha256",
        "manifestSha256",
    }
    schema = value.get("schemaVersion")
    if schema == SCHEMA:
        required_fields.update({"claimBindings", "claimSetSha256"})
    transport_evidence_version = value.get("transportEvidenceVersion")
    if transport_evidence_version is not None:
        required_fields.add("transportEvidenceVersion")
    if set(value) != required_fields or schema not in {LEGACY_SCHEMA, SCHEMA}:
        return ["DEEPDIVE_PROVENANCE_SCHEMA_INVALID"], evidence
    if transport_evidence_version not in {None, 1}:
        return ["DEEPDIVE_PROVENANCE_TRANSPORT_SCHEMA_INVALID"], evidence
    if value.get("status") != "Green":
        issues.append("DEEPDIVE_PROVENANCE_NOT_GREEN")
    if value.get("manifestSha256") != canonical_manifest_sha256(value):
        issues.append("DEEPDIVE_PROVENANCE_HASH_DRIFT")
    if value.get("articlePath") != _portable_article_path(article):
        issues.append("DEEPDIVE_ARTICLE_PATH_DRIFT")
    if value.get("articleSha256") != _canonical_text_sha256(article_bytes):
        issues.append("DEEPDIVE_ARTICLE_DRIFT")
    try:
        if value.get("issueDate") != _issue_date(article):
            issues.append("DEEPDIVE_ISSUE_DATE_DRIFT")
    except DeepDiveQualityError as error:
        issues.append(str(error))
    expected_locations = _article_url_locations(article_text)
    sources = value.get("sources")
    if not isinstance(sources, list):
        return issues + ["DEEPDIVE_PROVENANCE_SOURCES_INVALID"], evidence
    if value.get("sourceSetSha256") != _canonical_sha256(sources):
        issues.append("DEEPDIVE_SOURCE_SET_DRIFT")
    if schema == SCHEMA and value.get("claimSetSha256") != _canonical_sha256(
        value.get("claimBindings")
    ):
        issues.append("DEEPDIVE_CLAIM_SOURCE_SET_DRIFT")
    actual_urls: set[str] = set()
    required_source_fields = {
        "url",
        "publicHref",
        "finalUrl",
        "httpStatus",
        "fetchedAt",
        "contentSha256",
        "locations",
    }
    if transport_evidence_version == 1:
        required_source_fields.add("transportEvidence")
    for row in sources:
        if not isinstance(row, dict) or set(row) != required_source_fields:
            issues.append("DEEPDIVE_PROVENANCE_SOURCE_INVALID")
            continue
        url = str(row["url"])
        if url in actual_urls:
            issues.append(f"DEEPDIVE_PROVENANCE_DUPLICATE_URL {url}")
        actual_urls.add(url)
        if row.get("publicHref") != url:
            issues.append(f"DEEPDIVE_PUBLIC_HREF_DRIFT {url}")
        if row.get("locations") != expected_locations.get(url):
            issues.append(f"DEEPDIVE_URL_LOCATION_DRIFT {url}")
        status = row.get("httpStatus")
        if not isinstance(status, int) or not 200 <= status < 400:
            issues.append(f"DEEPDIVE_FETCH_STATUS_INVALID {url}")
        if HEX_64_RE.fullmatch(str(row.get("contentSha256") or "")) is None:
            issues.append(f"DEEPDIVE_CONTENT_HASH_INVALID {url}")
        if not str(row.get("finalUrl") or "").startswith(("http://", "https://")):
            issues.append(f"DEEPDIVE_FINAL_URL_INVALID {url}")
        if not str(row.get("fetchedAt") or "").strip():
            issues.append(f"DEEPDIVE_FETCH_TIME_INVALID {url}")
        if transport_evidence_version == 1:
            try:
                _normalize_fetch_records(
                    [
                        {
                            "url": row["url"],
                            "finalUrl": row["finalUrl"],
                            "httpStatus": row["httpStatus"],
                            "fetchedAt": row["fetchedAt"],
                            "contentSha256": row["contentSha256"],
                            "transportEvidence": row["transportEvidence"],
                        }
                    ],
                    expected_urls={url},
                )
            except DeepDiveQualityError:
                issues.append(f"DEEPDIVE_FETCH_TRANSPORT_EVIDENCE_INVALID {url}")
    if actual_urls != set(expected_locations):
        issues.append("DEEPDIVE_URL_SET_DRIFT")
    return sorted(set(issues)), evidence


def _build_tls_context() -> ssl.SSLContext:
    """端末固有CA差に依存しない取得contextを返す。"""

    try:
        import certifi
    except ModuleNotFoundError:
        # The recovery verifier runs with ``-I -S`` so startup hooks in the
        # venv cannot execute before trusted code. Windows' system CA store is
        # the bounded fallback for that isolated path.
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _is_generic_home_redirect(original_url: str, final_url: str) -> bool:
    original = urlsplit(original_url)
    final = urlsplit(final_url)
    original_path = original.path.rstrip("/")
    final_path = final.path.rstrip("/").casefold()
    return bool(original_path) and final_path in {"", "/index.html", "/index.htm"}


def _observed_record(
    *,
    url: str,
    final_url: str,
    status: int,
    body: bytes,
    transport_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    observed = body[:MAX_OBSERVED_BYTES]
    if not 200 <= status < 400 or not observed:
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} status={status} bytes={len(observed)}"
        )
    prefix = observed[:262144]
    if any(pattern.search(prefix) for pattern in SOFT_404_PATTERNS):
        raise DeepDiveQualityError(f"DEEPDIVE_URL_SOFT_404 {url}")
    if _is_generic_home_redirect(url, final_url):
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_GENERIC_REDIRECT {url} -> {final_url}"
        )
    return {
        "url": url,
        "finalUrl": final_url,
        "httpStatus": status,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "contentSha256": hashlib.sha256(observed).hexdigest(),
        "observedText": observed[:MAX_CLAIM_EVIDENCE_BYTES].decode(
            "utf-8", errors="replace"
        ),
        **(
            {"transportEvidence": transport_evidence}
            if transport_evidence is not None
            else {}
        ),
    }


def _run_system_transport(
    url: str,
    *,
    timeout: float,
) -> tuple[int, str, bytes]:
    """Python transportが限定的に拒否された時だけWindows HttpClientで一回取得する。"""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="news-grasp-provenance-",
        suffix=".body",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        helper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "ops"
            / "invoke-deepdive-system-fetch.ps1"
        )
        if not helper.is_file() or helper.is_symlink():
            raise DeepDiveQualityError("DEEPDIVE_SYSTEM_FETCH_HELPER_INVALID")
        result = proc.quiet_run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-Url",
                url,
                "-BodyPath",
                str(temporary),
                "-MaxBytes",
                str(MAX_OBSERVED_BYTES),
                "-TimeoutSec",
                str(max(1, int(timeout))),
            ],
            timeout=timeout + 3,
            check=False,
        )
        try:
            metadata = json.loads((result.stdout or "").strip())
        except json.JSONDecodeError:
            metadata = None
        if (
            result.returncode != 0
            or not isinstance(metadata, dict)
            or metadata.get("schemaVersion") != "DEEPDIVE_SYSTEM_FETCH_RESULT_V1"
            or metadata.get("transport") != "windows_system_http"
            or not isinstance(metadata.get("httpStatus"), int)
            or not str(metadata.get("finalUrl") or "").startswith(("http://", "https://"))
        ):
            detail = (result.stderr or result.stdout or "").strip()
            raise DeepDiveQualityError(
                f"DEEPDIVE_SYSTEM_FETCH_FAILED {url} exit={result.returncode} {detail}"
            )
        body = temporary.read_bytes()
        if metadata.get("bytes") != len(body):
            raise DeepDiveQualityError(
                f"DEEPDIVE_SYSTEM_FETCH_FAILED {url} body_length_drift"
            )
        return int(metadata["httpStatus"]), str(metadata["finalUrl"]), body
    finally:
        temporary.unlink(missing_ok=True)


def _fetch_one_system(
    url: str, *, timeout: float, primary_failure: str = "PRIMARY_TRANSPORT_FAILED"
) -> dict[str, object]:
    status, final_url, body = _run_system_transport(url, timeout=timeout)
    return _observed_record(
        url=url,
        final_url=final_url,
        status=status,
        body=body,
        transport_evidence={
            "selectedTransport": "windows_system_http",
            "primaryTransport": "python_urllib",
            "primaryFailure": primary_failure,
            "fallbackAttemptCount": 1,
        },
    )


def _fetch_one(url: str, *, timeout: float) -> dict[str, object]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urlopen(
            request,
            timeout=timeout,
            context=_build_tls_context(),
        ) as response:
            body = bytearray()
            while len(body) < MAX_OBSERVED_BYTES:
                chunk = response.read(min(65536, MAX_OBSERVED_BYTES - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            status = int(response.getcode() or 0)
            final_url = response.geturl()
    except HTTPError as error:
        if error.code in (404, 410):
            raise DeepDiveQualityError(
                f"DEEPDIVE_URL_FETCH_FAILED {url} HTTPError: {error}"
            ) from error
        if error.code == 403:
            return _fetch_one_system(
                url, timeout=timeout, primary_failure=f"HTTP_{error.code}"
            )
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} HTTPError: {error}"
        ) from error
    except URLError as error:
        if "CERTIFICATE_VERIFY_FAILED" in str(error):
            return _fetch_one_system(
                url, timeout=timeout, primary_failure="CERTIFICATE_VERIFY_FAILED"
            )
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} {type(error).__name__}: {error}"
        ) from error
    except TimeoutError as error:
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} TimeoutError: {error}"
        ) from error
    except OSError as error:
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} {type(error).__name__}: {error}"
        ) from error
    return _observed_record(
        url=url,
        final_url=final_url,
        status=status,
        body=bytes(body),
        transport_evidence={
            "selectedTransport": "python_urllib",
            "primaryTransport": "python_urllib",
            "primaryFailure": None,
            "fallbackAttemptCount": 0,
        },
    )


def capture_provenance(
    *,
    article_path: Path,
    output_path: Path,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """記事内URLを重複排除して一回ずつ取得し、manifestを作る。"""

    article = Path(article_path).resolve(strict=True)
    locations = _article_url_locations(article.read_text(encoding="utf-8-sig"))
    records = [_fetch_one(url, timeout=timeout) for url in sorted(locations)]
    return build_provenance_manifest(
        article_path=article,
        fetch_records=records,
        output_path=output_path,
    )


def materialize_issue_bundle(
    *,
    repo_root: Path,
    issue_date: str,
    timeout: float = 20.0,
    context_pack_path: Path | None = None,
    render_public: bool = False,
) -> dict[str, Any]:
    """article由来のclaim/provenance/dialogueを単一handlerで確定する。

    article本文に存在するclaim-sourceとnetwork observationを検証し、
    provenanceをstageしてから同一quality predicateでGreenを決める。
    claim-sourceの不足は本文を補完せず、追加調査へ戻すtyped Redにする。
    途中crashで一部が置き換わってもbundle receiptは作られず、
    article/provenance/dialogueのhash不一致により後続auditはRedになる。
    """

    repo = Path(repo_root).resolve(strict=True)
    issue_day = date.fromisoformat(issue_date)
    article = _safe_repo_path(
        repo / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md",
        repo_root=repo,
    )
    if not article.is_file() or article.is_symlink():
        raise DeepDiveQualityError(f"DEEPDIVE_ARTICLE_MISSING {article}")
    article_text = article.read_text(encoding="utf-8-sig")
    original_article_text = article_text
    declarations = _claim_source_declarations(article_text)

    manifest = _safe_repo_path(
        repo / "data" / "deepdive-provenance" / f"{issue_date}.json",
        repo_root=repo,
    )
    dialogue = _safe_repo_path(
        article.with_name(f"{issue_date}-DeepDive-dialogue.md"),
        repo_root=repo,
    )
    claim_transport = _safe_repo_path(
        repo / "data" / "deepdive-claim-source" / f"{issue_date}.json",
        repo_root=repo,
    )
    receipt_path = _safe_repo_path(
        repo / "data" / "deepdive-bundles" / f"{issue_date}.json",
        repo_root=repo,
    )
    rendered_directory = _safe_repo_path(
        repo / "docs" / "deepdive" / issue_date,
        repo_root=repo,
    )
    final_snapshots = {
        path: _snapshot_materializer_file(path, repo_root=repo)
        for path in (
            article,
            manifest,
            dialogue,
            claim_transport,
            receipt_path,
        )
    }
    rendered_snapshots = (
        _snapshot_rendered_files(rendered_directory, repo_root=repo)
        if render_public
        else {}
    )
    staged_manifest: Path | None = None
    staged_dialogue: Path | None = None
    staged_claim_transport: Path | None = None
    try:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        dialogue.parent.mkdir(parents=True, exist_ok=True)
        claim_transport.parent.mkdir(parents=True, exist_ok=True)
        manifest = _safe_repo_path(manifest, repo_root=repo)
        dialogue = _safe_repo_path(dialogue, repo_root=repo)
        claim_transport = _safe_repo_path(claim_transport, repo_root=repo)
        staged_manifest = _exclusive_stage_file(manifest, repo_root=repo)
        staged_dialogue = _exclusive_stage_file(dialogue, repo_root=repo)
        staged_claim_transport = _exclusive_stage_file(
            claim_transport, repo_root=repo
        )
        locations = _article_url_locations(article_text)
        transported = None
        if issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE:
            transported = _load_claim_source_transport(
                path=claim_transport,
                article_text=article_text,
                article_path=article,
                issue_date=issue_date,
            )
        fetch_locations = sorted(locations)
        if (
            issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE
            and not declarations
            and transported is None
        ):
            # binding不足時はresearchへ戻すため、canonical fetchは一度だけ許可する。
            fetch_locations = fetch_locations[:1]
        records = [_fetch_one(url, timeout=timeout) for url in fetch_locations]
        observed = _normalize_fetch_records(
            records,
            expected_urls=set(fetch_locations),
        )
        if issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE:
            if transported is not None:
                article_text = _restore_claim_sources_from_transport(
                    article_text, bindings=transported
                )
            elif not declarations:
                # 取得本文からclaim/evidenceを推測して記事へ書き戻すと、
                # 観測根拠のない意味を生成してしまう。research routeへ戻す。
                raise DeepDiveQualityError(
                    "DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT bindings_missing"
                )
        article_bytes_override = (
            article_text.encode("utf-8")
            if article_text != original_article_text
            else None
        )
        # transportはclaimとsource本文のfitを実測した後だけ封印する。
        _build_claim_bindings(
            article_text=article_text,
            issue_date=issue_date,
            observed=observed,
        )
        _atomic_write_json(
            staged_claim_transport,
            _claim_source_transport_value(
                article_text=article_text,
                article_path=article,
                issue_date=issue_date,
            ),
        )
        build_provenance_manifest(
            article_path=article,
            fetch_records=records,
            output_path=staged_manifest,
            article_bytes_override=article_bytes_override,
        )
        claim_issues = validate_claim_source_fit(
            article,
            staged_manifest,
            article_text_override=article_text,
        )
        if claim_issues:
            raise DeepDiveQualityError("; ".join(claim_issues))
        if (
            not dialogue.is_file()
            or dialogue.is_symlink()
            or dialogue.stat().st_size <= 0
        ):
            raise DeepDiveQualityError(
                "DEEPDIVE_LLM_REWRITE_REQUIRED dialogue_staged_missing"
            )
        # LLM生成済みcanonical dialogueだけを入力として採用し、
        # validatorを通すexclusive stageへ内容をそのまま複製する。
        staged_dialogue.write_bytes(dialogue.read_bytes())
        dialogue_issues = deepdive_dialogue.validate_dialogue_document(
            staged_dialogue.read_text(encoding="utf-8-sig"),
            source_markdown=article_text,
        )
        dialogue_issues.extend(
            _dialogue_source_lineage_issues(staged_dialogue, staged_manifest)
        )
        if dialogue_issues:
            raise DeepDiveQualityError("; ".join(dialogue_issues))
        staged_manifest = _safe_repo_path(
            staged_manifest, repo_root=repo, file_required=True
        )
        staged_dialogue = _safe_repo_path(
            staged_dialogue, repo_root=repo, file_required=True
        )
        staged_claim_transport = _safe_repo_path(
            staged_claim_transport, repo_root=repo, file_required=True
        )
        manifest = _safe_repo_path(manifest, repo_root=repo)
        dialogue = _safe_repo_path(dialogue, repo_root=repo)
        claim_transport = _safe_repo_path(claim_transport, repo_root=repo)
        quality_review_issues, _, quality_review, _ = _validate_quality_review(
            repo=repo,
            issue_date=issue_date,
            article=article,
            dialogue=dialogue,
            article_bytes_override=article_bytes_override,
        )
        if quality_review_issues or quality_review.get("status") != "Green":
            review_failure = "; ".join(quality_review_issues)
            raise DeepDiveQualityError(
                review_failure or "DEEPDIVE_QUALITY_REVIEW_RED"
            )
        if article_bytes_override is not None:
            _atomic_write_text(article, article_text)
        os.replace(staged_manifest, manifest)
        os.replace(staged_dialogue, dialogue)
        os.replace(staged_claim_transport, claim_transport)

        if render_public:
            from tools.render_deepdive import build_deepdive_pages

            expected_rendered = _safe_repo_path(
                rendered_directory / "index.html",
                repo_root=repo,
            )
            written = build_deepdive_pages(
                docs_root=repo / "docs",
                digest_dir=repo / "digest" / "DeepDive",
                issue_date=issue_date,
                validate_live_urls=False,
            )
            safe_written = {
                _safe_repo_path(path, repo_root=repo, file_required=True).resolve()
                for path in written
            }
            if expected_rendered.resolve() not in safe_written:
                raise DeepDiveQualityError("DEEPDIVE_RENDERED_HTML_MISSING")

        report = audit_issue(
            repo_root=repo,
            issue_date=issue_date,
            include_corpus=False,
            require_rendered_public=render_public,
            route="production_generation",
        )
        if report["status"] != "Green":
            raise DeepDiveQualityError("; ".join(report["issues"]))
        receipt: dict[str, Any] = {
            "schemaVersion": BUNDLE_SCHEMA,
            "status": "Green",
            "issueDate": issue_date,
            "handler": "tools.deepdive_quality.materialize-issue",
            "articlePath": _portable_article_path(article),
            "articleSha256": _file_sha256(article),
            "claimSourceTransportPath": claim_transport.relative_to(repo).as_posix(),
            "claimSourceTransportSha256": _file_sha256(claim_transport),
            "provenancePath": manifest.relative_to(repo).as_posix(),
            "provenanceSha256": _file_sha256(manifest),
            "dialoguePath": dialogue.relative_to(repo).as_posix(),
            "dialogueSha256": _file_sha256(dialogue),
            "renderedPublicRequired": render_public,
        }
        if render_public:
            rendered = rendered_directory / "index.html"
            receipt["renderedPublicPath"] = rendered.relative_to(repo).as_posix()
            receipt["renderedPublicSha256"] = _file_sha256(rendered)
        receipt["bundleSha256"] = _canonical_sha256(receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path = _safe_repo_path(receipt_path, repo_root=repo)
        _atomic_write_json(receipt_path, receipt)
        return {**receipt, "bundleReceiptPath": str(receipt_path)}
    except Exception:
        for path, payload in final_snapshots.items():
            _restore_materializer_file(path, payload, repo_root=repo)
        if render_public:
            _restore_rendered_files(
                rendered_directory,
                rendered_snapshots,
                repo_root=repo,
            )
        raise
    finally:
        for staged in (
            staged_manifest,
            staged_dialogue,
            staged_claim_transport,
        ):
            if staged is not None:
                staged.unlink(missing_ok=True)


def capture_period_provenance(
    *,
    repo_root: Path,
    start_date: str,
    end_date: str,
    timeout: float = 20.0,
    observation_cache_path: Path | None = None,
) -> dict[str, Any]:
    """期間内のURLを全記事で重複排除し、一URL一取得でmanifestを作る。"""

    repo = Path(repo_root).resolve()
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise DeepDiveQualityError("DEEPDIVE_PERIOD_INVALID")
    articles: list[Path] = []
    article_urls: dict[Path, set[str]] = {}
    current = start
    while current <= end:
        article = (
            repo
            / "digest"
            / "DeepDive"
            / f"{current.isoformat()}-DeepDive.md"
        )
        if not article.is_file():
            raise DeepDiveQualityError(f"DEEPDIVE_ARTICLE_MISSING {article}")
        articles.append(article)
        article_urls[article] = set(
            _article_url_locations(
                article.read_text(encoding="utf-8-sig")
            )
        )
        current += timedelta(days=1)
    unique_urls = sorted({url for urls in article_urls.values() for url in urls})
    claim_source_urls = {
        row["sourceUrl"]
        for article in articles
        for row in _claim_source_declarations(
            article.read_text(encoding="utf-8-sig")
        )
    }
    cache_path = (
        Path(observation_cache_path).resolve()
        if observation_cache_path is not None
        else repo
        / "build"
        / "deepdive-provenance-observations"
        / f"{start_date}_{end_date}.json"
    )
    observed = _seed_observations_from_manifests(repo=repo, articles=articles)
    observed.update(_load_observation_cache(cache_path))
    observed = {
        url: record for url, record in observed.items() if url in unique_urls
    }
    pending_urls = [
        url
        for url in unique_urls
        if url not in observed
        or (url in claim_source_urls and not observed[url].get("observedText"))
    ]
    reused_url_count = len(observed) - len(
        [url for url in pending_urls if url in observed]
    )
    failures: list[dict[str, str]] = []
    worker_count = max(1, min(8, len(pending_urls)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_fetch_one, url, timeout=timeout): url
            for url in pending_urls
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                observed[url] = future.result()
            except DeepDiveQualityError as error:
                failures.append({"url": url, "error": str(error)})
    failures.sort(key=lambda row: row["url"])
    _write_observation_cache(cache_path, observed)
    manifests: list[str] = []
    for article in articles:
        if not article_urls[article].issubset(observed):
            continue
        output = (
            repo
            / "data"
            / "deepdive-provenance"
            / f"{_issue_date(article)}.json"
        )
        build_provenance_manifest(
            article_path=article,
            fetch_records=[observed[url] for url in sorted(article_urls[article])],
            output_path=output,
        )
        manifests.append(str(output))
    return {
        "schemaVersion": "DEEPDIVE_PERIOD_PROVENANCE_CAPTURE_V1",
        "status": "Green" if not failures else "Red",
        "startDate": start_date,
        "endDate": end_date,
        "articleCount": len(articles),
        "uniqueUrlCount": len(unique_urls),
        "reusedUrlCount": reused_url_count,
        "fetchedUrlCount": len(pending_urls),
        "failedUrlCount": len(failures),
        "failures": failures,
        "observationCachePath": str(cache_path),
        "manifestPaths": manifests,
    }


def _dialogue_paths_for_period(
    *,
    repo_root: Path,
    start: date,
    end: date,
) -> list[Path]:
    """存在する対談だけを日付順に一度収集する。"""

    deepdive_dir = Path(repo_root).resolve() / "digest" / "DeepDive"
    paths: list[Path] = []
    current = start
    while current <= end:
        path = deepdive_dir / f"{current.isoformat()}-DeepDive-dialogue.md"
        if path.is_file():
            paths.append(path)
        current += timedelta(days=1)
    return paths


def _history_regular_file(path: Path, *, repo_root: Path) -> bool:
    """履歴列挙で外部へ追跡しないregular fileだけを許可する。"""

    try:
        if path.is_symlink() or not path.is_file():
            return False
        metadata = os.lstat(path)
        if int(getattr(metadata, "st_file_attributes", 0)) & 0x400:
            return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _history_article_paths(
    *,
    repo_root: Path,
    start: date,
    end: date,
) -> list[Path]:
    """履歴範囲内のcanonical記事だけをPath.globで収集する。"""

    deepdive_dir = repo_root / "digest" / "DeepDive"
    try:
        if not deepdive_dir.exists() or deepdive_dir.is_symlink():
            return []
        if not deepdive_dir.is_dir():
            return []
        metadata = os.lstat(deepdive_dir)
        if int(getattr(metadata, "st_file_attributes", 0)) & 0x400:
            return []
        deepdive_dir.resolve(strict=True).relative_to(repo_root)
        candidates = list(deepdive_dir.glob("*-DeepDive.md"))
    except (OSError, RuntimeError, ValueError):
        return []

    dated_paths: list[tuple[date, Path]] = []
    for path in candidates:
        match = ISSUE_DATE_RE.fullmatch(path.name)
        if match is None or not _history_regular_file(
            path,
            repo_root=repo_root,
        ):
            continue
        try:
            issue_day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if start <= issue_day <= end:
            dated_paths.append((issue_day, path))
    dated_paths.sort(key=lambda row: (row[0], row[1].name))
    return [path for _issue_day, path in dated_paths]


def _validate_history_output_path(
    output: Path | str,
    *,
    repo_root: Path,
) -> Path:
    """履歴Red manifestの書込み先をrepo内の固定prefixへ限定する。"""

    try:
        relative = Path(output)
    except (TypeError, ValueError) as error:
        raise DeepDiveQualityError(
            "DEEPDIVE_HISTORY_OUTPUT_INVALID"
        ) from error
    prefix_parts = HISTORY_OUTPUT_PREFIX.parts
    if (
        relative.is_absolute()
        or len(relative.parts) <= len(prefix_parts)
        or relative.parts[: len(prefix_parts)] != prefix_parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix != ".json"
    ):
        raise DeepDiveQualityError("DEEPDIVE_HISTORY_OUTPUT_INVALID")

    repo = Path(repo_root).resolve()
    candidate = repo / relative
    try:
        candidate = _safe_repo_path(candidate, repo_root=repo)
    except DeepDiveQualityError as error:
        raise DeepDiveQualityError(
            "DEEPDIVE_HISTORY_OUTPUT_INVALID"
        ) from error
    if candidate.is_symlink() or (
        candidate.exists() and not candidate.is_file()
    ):
        raise DeepDiveQualityError("DEEPDIVE_HISTORY_OUTPUT_INVALID")
    return candidate


def _dialogue_source_lineage_issues(
    dialogue_path: Path,
    manifest_path: Path,
) -> list[str]:
    if not dialogue_path.is_file() or not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        dialogue_text = dialogue_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["DEEPDIVE_DIALOGUE_SOURCE_LINEAGE_INVALID"]
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != SCHEMA:
        return []
    match = re.search(
        r'^source_sha256:\s*["\']?([a-f0-9]{64})["\']?\s*$',
        dialogue_text,
        re.MULTILINE,
    )
    if match is None:
        return ["DEEPDIVE_DIALOGUE_SOURCE_SHA_MISSING"]
    if match.group(1).casefold() != str(manifest.get("articleSha256") or "").casefold():
        return ["DEEPDIVE_DIALOGUE_SOURCE_DRIFT"]
    return []


def _quality_review_file_evidence(
    path: Path,
    payload: bytes,
) -> dict[str, str]:
    evidence = {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    try:
        evidence["canonicalTextSha256"] = _canonical_text_sha256(payload)
    except UnicodeError:
        pass
    return evidence


def _relation_review_payload(
    article: Path,
    *,
    article_bytes_override: bytes | None = None,
) -> object:
    """記事の先頭relations blockをrendererと同じparserで取得する。"""

    from tools.render_deepdive import extract_blocks

    article_text = (
        article.read_text(encoding="utf-8-sig")
        if article_bytes_override is None
        else article_bytes_override.decode("utf-8-sig")
    )
    blocks = extract_blocks(article_text)
    relations = blocks.get("relations") or []
    if not relations or not isinstance(relations[0], dict):
        raise ValueError("relations block missing or malformed")
    return relations[0]


def _quality_review_artifact_sha256(
    kind: str,
    *,
    article: Path,
    dialogue: Path,
    article_bytes_override: bytes | None = None,
    dialogue_bytes_override: bytes | None = None,
) -> str:
    path = article if kind in {"article", "relation"} else dialogue
    if (
        article_bytes_override is None
        and dialogue_bytes_override is None
        and (not path.is_file() or path.is_symlink())
    ):
        raise OSError(f"{kind} artifact missing")
    if kind == "relation":
        relation = _relation_review_payload(
            article,
            article_bytes_override=article_bytes_override,
        )
        return _canonical_sha256(relation)
    if kind == "article" and article_bytes_override is not None:
        return hashlib.sha256(article_bytes_override).hexdigest()
    if kind == "dialogue" and dialogue_bytes_override is not None:
        return hashlib.sha256(dialogue_bytes_override).hexdigest()
    if not path.is_file() or path.is_symlink():
        raise OSError(f"{kind} artifact missing")
    return _file_sha256(path)


def _validate_quality_review(
    *,
    repo: Path,
    issue_date: str,
    article: Path,
    dialogue: Path,
    article_bytes_override: bytes | None = None,
    dialogue_bytes_override: bytes | None = None,
) -> tuple[list[str], set[str], dict[str, Any], dict[str, str] | None]:
    """DEEPDIVE_QUALITY_REVIEW_V2をartifact identityと同時に検証する。"""

    review_path = repo / "data" / "deepdive-quality-review" / f"{issue_date}.json"
    all_codes = set(DEEPDIVE_QUALITY_REVIEW_ISSUE_CODES.values())
    review_issues: list[str] = []
    review_codes: set[str] = set()
    review_evidence: dict[str, str] | None = None
    quality_review: dict[str, Any] = {
        "schemaVersion": DEEPDIVE_QUALITY_REVIEW_V2,
        "issueDate": issue_date,
        "computedAverageScore": None,
        "status": "Red",
    }

    def add_issue(issue: str) -> None:
        if issue not in review_issues:
            review_issues.append(issue)

    def mark_global(issue: str) -> None:
        add_issue(issue)
        review_codes.update(all_codes)

    try:
        if not review_path.is_file() or review_path.is_symlink():
            add_issue("DEEPDIVE_QUALITY_REVIEW_MISSING")
            review_codes.update(all_codes)
            quality_review["issues"] = list(review_issues)
            return review_issues, review_codes, quality_review, None
        raw_review = review_path.read_bytes()
    except (OSError, UnicodeError):
        add_issue("DEEPDIVE_QUALITY_REVIEW_MISSING")
        review_codes.update(all_codes)
        quality_review["issues"] = list(review_issues)
        return review_issues, review_codes, quality_review, None

    review_evidence = _quality_review_file_evidence(review_path, raw_review)
    try:
        payload = json.loads(raw_review.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError):
        mark_global("DEEPDIVE_QUALITY_REVIEW_SCHEMA_INVALID")
        quality_review["issues"] = list(review_issues)
        return review_issues, review_codes, quality_review, review_evidence
    if not isinstance(payload, dict):
        mark_global("DEEPDIVE_QUALITY_REVIEW_SCHEMA_INVALID")
        quality_review["issues"] = list(review_issues)
        return review_issues, review_codes, quality_review, review_evidence

    quality_review = dict(payload)
    expected_review_keys = {
        "schemaVersion",
        "issueDate",
        "artifacts",
        "scores",
        "findings",
        "averageScore",
        "reviewRoute",
        "status",
    }
    if set(payload) != expected_review_keys:
        mark_global("DEEPDIVE_QUALITY_REVIEW_SCHEMA_INVALID")
    if payload.get("schemaVersion") != DEEPDIVE_QUALITY_REVIEW_V2:
        mark_global("DEEPDIVE_QUALITY_REVIEW_SCHEMA_INVALID")
    if payload.get("issueDate") != issue_date:
        mark_global("DEEPDIVE_QUALITY_REVIEW_ISSUE_DATE_MISMATCH")

    expected_paths = {
        "article": f"digest/DeepDive/{issue_date}-DeepDive.md",
        "relation": f"digest/DeepDive/{issue_date}-DeepDive.md",
        "dialogue": f"digest/DeepDive/{issue_date}-DeepDive-dialogue.md",
    }
    artifact_rows: dict[str, dict[str, Any]] = {}
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(
        DEEPDIVE_QUALITY_REVIEW_ARTIFACTS
    ):
        mark_global("DEEPDIVE_QUALITY_REVIEW_ARTIFACTS_INVALID")
    else:
        for kind in DEEPDIVE_QUALITY_REVIEW_ARTIFACTS:
            row = artifacts.get(kind)
            if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
                mark_global(f"DEEPDIVE_QUALITY_REVIEW_ARTIFACT_INVALID {kind}")
                continue
            artifact_rows[kind] = row
            if row.get("path") != expected_paths[kind]:
                mark_global(f"DEEPDIVE_QUALITY_REVIEW_PATH_INVALID {kind}")
            if not isinstance(row.get("sha256"), str) or not HEX_64_RE.fullmatch(
                row["sha256"]
            ):
                mark_global(f"DEEPDIVE_QUALITY_REVIEW_SHA_INVALID {kind}")

    scores_valid = False
    scores = payload.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(
        DEEPDIVE_QUALITY_REVIEW_AXES
    ):
        mark_global("DEEPDIVE_QUALITY_REVIEW_SCORES_INVALID")
    else:
        scores_valid = True
        for axis in DEEPDIVE_QUALITY_REVIEW_AXES:
            value = scores[axis]
            if type(value) is not int or not 1 <= value <= 5:
                scores_valid = False
                mark_global(f"DEEPDIVE_QUALITY_REVIEW_SCORE_INVALID {axis}")

    findings_valid = False
    findings = payload.get("findings")
    if not isinstance(findings, dict) or set(findings) != set(
        DEEPDIVE_QUALITY_REVIEW_AXES
    ):
        mark_global("DEEPDIVE_QUALITY_REVIEW_FINDINGS_INVALID")
    else:
        findings_valid = True
        for axis in DEEPDIVE_QUALITY_REVIEW_AXES:
            if not isinstance(findings[axis], str) or not findings[axis].strip():
                findings_valid = False
                mark_global(f"DEEPDIVE_QUALITY_REVIEW_FINDING_INVALID {axis}")

    review_route = payload.get("reviewRoute")
    if not isinstance(review_route, str) or review_route not in DEEPDIVE_QUALITY_REVIEW_ROUTES:
        mark_global(f"DEEPDIVE_QUALITY_REVIEW_ROUTE_UNKNOWN {review_route}")

    computed_average: float | None = None
    if scores_valid:
        computed_average = sum(scores.values()) / len(DEEPDIVE_QUALITY_REVIEW_AXES)
        quality_review["computedAverageScore"] = computed_average
        if computed_average < 4:
            add_issue(
                "DEEPDIVE_QUALITY_REVIEW_AVERAGE_TOO_LOW "
                f"{computed_average:.1f}"
            )
            review_codes.update(all_codes)
        for axis in DEEPDIVE_QUALITY_REVIEW_AXES:
            value = scores[axis]
            if value < 3:
                add_issue(f"DEEPDIVE_QUALITY_REVIEW_SCORE_TOO_LOW {axis}={value}")
                if axis in DEEPDIVE_QUALITY_REVIEW_ARTICLE_AXES:
                    review_codes.add(DEEPDIVE_QUALITY_REVIEW_ISSUE_CODES["article"])
                elif axis == "dialogue_naturalness":
                    review_codes.add(
                        DEEPDIVE_QUALITY_REVIEW_ISSUE_CODES["dialogue"]
                    )
                elif axis == "relation_map_utility":
                    review_codes.add(
                        DEEPDIVE_QUALITY_REVIEW_ISSUE_CODES["relation"]
                    )

    for kind, row in artifact_rows.items():
        if row.get("path") != expected_paths[kind]:
            continue
        declared_sha = row.get("sha256")
        if not isinstance(declared_sha, str) or not HEX_64_RE.fullmatch(declared_sha):
            continue
        try:
            actual_sha = _quality_review_artifact_sha256(
                kind,
                article=article,
                dialogue=dialogue,
                article_bytes_override=article_bytes_override,
                dialogue_bytes_override=dialogue_bytes_override,
            )
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            actual_sha = None
        if actual_sha is None or actual_sha.casefold() != declared_sha.casefold():
            add_issue(f"DEEPDIVE_QUALITY_REVIEW_ARTIFACT_STALE {kind}")
            review_codes.add(DEEPDIVE_QUALITY_REVIEW_ISSUE_CODES[kind])

    structural_ok = not review_issues
    computed_status = (
        "Green"
        if scores_valid
        and findings_valid
        and structural_ok
        and computed_average is not None
        and computed_average >= 4
        and all(scores[axis] >= 3 for axis in DEEPDIVE_QUALITY_REVIEW_AXES)
        else "Red"
    )
    declared_average = payload.get("averageScore")
    declared_average_ok = (
        isinstance(declared_average, (int, float))
        and not isinstance(declared_average, bool)
        and math.isfinite(float(declared_average))
        and computed_average is not None
        and math.isclose(float(declared_average), computed_average, rel_tol=0, abs_tol=1e-9)
    )
    if not declared_average_ok or payload.get("status") != computed_status:
        mark_global("DEEPDIVE_QUALITY_REVIEW_SUMMARY_MISMATCH")
        computed_status = "Red"
    quality_review["computedAverageScore"] = computed_average
    quality_review["status"] = computed_status
    quality_review["issues"] = list(review_issues)
    return review_issues, review_codes, quality_review, review_evidence


def audit_issue(
    *,
    repo_root: Path,
    issue_date: str,
    include_corpus: bool = True,
    require_rendered_public: bool = False,
    route: str = "codex_daily_audit",
    require_v2_claim_sources: bool = False,
) -> dict[str, Any]:
    """production runnerと日次監査が共有する一日分の品質判定。"""

    _validate_shared_quality_route_registry(route)
    issue_day = date.fromisoformat(issue_date)
    repo = Path(repo_root).resolve()
    article = repo / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    dialogue = repo / "digest" / "DeepDive" / f"{issue_date}-DeepDive-dialogue.md"
    manifest = repo / "data" / "deepdive-provenance" / f"{issue_date}.json"
    quality_review_path = (
        repo / "data" / "deepdive-quality-review" / f"{issue_date}.json"
    )
    rendered_public = repo / "docs" / "deepdive" / issue_date / "index.html"
    issue_codes: list[str] = []
    issues: list[str] = []
    provenance_issues, provenance_evidence = _validate_provenance_with_evidence(
        article,
        manifest,
    )
    if provenance_issues:
        issue_codes.append("deepdive_url_provenance_invalid")
        issues.extend(provenance_issues)
    claim_source_issues = validate_claim_source_fit(
        article,
        manifest,
        require_v2_claim_sources=require_v2_claim_sources,
    )
    if (
        not claim_source_issues
        and (require_v2_claim_sources or issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE)
        and article.is_file()
        and not manifest.is_file()
    ):
        try:
            has_bindings = bool(
                _claim_source_declarations(article.read_text(encoding="utf-8-sig"))
            )
        except (OSError, UnicodeError, DeepDiveQualityError) as error:
            claim_source_issues = [str(error)]
        else:
            if not has_bindings:
                claim_source_issues = ["DEEPDIVE_CLAIM_SOURCE_BINDINGS_MISSING"]
    if claim_source_issues:
        article_value_issues = [
            issue
            for issue in claim_source_issues
            if issue.startswith("DEEPDIVE_ARTICLE_VALUE_INVALID")
        ]
        if article_value_issues:
            if "deepdive_article_value_invalid" not in issue_codes:
                issue_codes.append("deepdive_article_value_invalid")
        elif any(
            issue == "DEEPDIVE_CLAIM_SOURCE_BINDINGS_MISSING"
            or "bindings_missing" in issue
            or issue.startswith("DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT")
            or issue == "DEEPDIVE_CLAIM_SOURCE_MANIFEST_LEGACY"
            for issue in claim_source_issues
        ):
            if "deepdive_research_evidence_insufficient" not in issue_codes:
                issue_codes.append("deepdive_research_evidence_insufficient")
        elif "deepdive_url_provenance_invalid" not in issue_codes:
            issue_codes.append("deepdive_url_provenance_invalid")
        issues.extend(claim_source_issues)
    rendered_evidence: list[dict[str, str]] = []
    if require_rendered_public:
        rendered_issues, rendered_evidence = validate_rendered_public_surface(
            manifest,
            rendered_public,
        )
        if rendered_issues:
            issue_codes.append("deepdive_public_surface_invalid")
            issues.extend(rendered_issues)
    if not dialogue.is_file() or not article.is_file():
        dialogue_issues = ["DEEPDIVE_DIALOGUE_OR_SOURCE_MISSING"]
    else:
        dialogue_issues = deepdive_dialogue.validate_dialogue_document(
            dialogue.read_text(encoding="utf-8-sig"),
            source_markdown=article.read_text(encoding="utf-8-sig"),
        )
        dialogue_issues.extend(
            _dialogue_source_lineage_issues(dialogue, manifest)
        )
    if dialogue_issues:
        issue_codes.append("deepdive_dialogue_value_invalid")
        issues.extend(dialogue_issues)
    (
        quality_review_issues,
        quality_review_codes,
        quality_review,
        quality_review_evidence,
    ) = _validate_quality_review(
        repo=repo,
        issue_date=issue_date,
        article=article,
        dialogue=dialogue,
    )
    for code in sorted(quality_review_codes):
        if code not in issue_codes:
            issue_codes.append(code)
    issues.extend(quality_review_issues)
    dialogue_corpus_audit: dict[str, object] | None = None
    audited_files = {
        row["path"]: row for row in provenance_evidence
    }
    for row in rendered_evidence:
        audited_files[row["path"]] = row
    if quality_review_evidence is not None:
        audited_files[quality_review_evidence["path"]] = quality_review_evidence
    if include_corpus:
        dialogue_paths = _dialogue_paths_for_period(
            repo_root=repo,
            start=issue_day - timedelta(days=30),
            end=issue_day,
        )
        dialogue_corpus_audit = deepdive_dialogue.audit_dialogue_corpus(
            dialogue_paths,
            repo_root=repo,
        )
        for row in dialogue_corpus_audit.get("audited_files", []):
            audited_files[str(row["path"])] = dict(row)
        corpus_issues = list(dialogue_corpus_audit["issues"])
        if corpus_issues:
            if "deepdive_dialogue_value_invalid" not in issue_codes:
                issue_codes.append("deepdive_dialogue_value_invalid")
            issues.extend(f"CORPUS: {issue}" for issue in corpus_issues)
    return {
        "schemaVersion": REPORT_SCHEMA,
        "status": "Green" if not issue_codes else "Red",
        "issueDate": issue_date,
        "route": route,
        "issueCodes": issue_codes,
        "issues": issues,
        "articlePath": str(article),
        "dialoguePath": str(dialogue),
        "provenancePath": str(manifest),
        "qualityReviewPath": str(quality_review_path),
        "qualityReview": quality_review,
        "renderedPublicPath": str(rendered_public),
        "dialogueCorpusAudit": dialogue_corpus_audit,
        "auditedFiles": [audited_files[path] for path in sorted(audited_files)],
        "auditedPaths": sorted(audited_files),
    }


def audit_period(
    *,
    repo_root: Path,
    start_date: str,
    end_date: str,
    require_rendered_public: bool = False,
    route: str = "codex_daily_audit",
) -> dict[str, Any]:
    _validate_shared_quality_route_registry(route)
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise DeepDiveQualityError("DEEPDIVE_PERIOD_INVALID")
    if (end - start).days + 1 > MAX_AUDIT_PERIOD_DAYS:
        raise DeepDiveQualityError(
            f"DEEPDIVE_PERIOD_TOO_LARGE max={MAX_AUDIT_PERIOD_DAYS}"
        )
    rows: list[dict[str, Any]] = []
    current = start
    while current <= end:
        rows.append(
            audit_issue(
                repo_root=repo_root,
                issue_date=current.isoformat(),
                include_corpus=False,
                require_rendered_public=require_rendered_public,
                route=route,
            )
        )
        current += timedelta(days=1)
    issue_codes = sorted({code for row in rows for code in row["issueCodes"]})
    dialogue_corpus_audit = deepdive_dialogue.audit_dialogue_corpus(
        _dialogue_paths_for_period(
            repo_root=repo_root,
            start=start,
            end=end,
        ),
        repo_root=Path(repo_root).resolve(),
    )
    if dialogue_corpus_audit["issues"]:
        issue_codes = sorted(
            {*issue_codes, "deepdive_dialogue_value_invalid"}
        )
    return {
        "schemaVersion": "DEEPDIVE_SHARED_QUALITY_PERIOD_REPORT_V1",
        "status": "Green" if not issue_codes else "Red",
        "startDate": start_date,
        "endDate": end_date,
        "route": route,
        "issueCodes": issue_codes,
        "days": rows,
        "dialogueCorpusAudit": dialogue_corpus_audit,
    }


def audit_history(
    *,
    repo_root: Path,
    start_date: str,
    end_date: str,
    require_rendered_public: bool = False,
    route: str = "codex_daily_audit",
) -> dict[str, Any]:
    """実在するcanonical記事だけを横断監査する（期間上限なし）。"""

    _validate_shared_quality_route_registry(route)
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError) as error:
        raise DeepDiveQualityError("DEEPDIVE_HISTORY_PERIOD_INVALID") from error
    if end < start:
        raise DeepDiveQualityError("DEEPDIVE_HISTORY_PERIOD_INVALID")

    repo = Path(repo_root).resolve()
    article_paths = _history_article_paths(
        repo_root=repo,
        start=start,
        end=end,
    )
    rows: list[dict[str, Any]] = []
    dialogue_paths: list[Path] = []
    for article_path in article_paths:
        match = ISSUE_DATE_RE.fullmatch(article_path.name)
        if match is None:
            continue
        issue_date = match.group(1)
        rows.append(
            audit_issue(
                repo_root=repo_root,
                issue_date=issue_date,
                include_corpus=False,
                require_rendered_public=require_rendered_public,
                route=route,
                require_v2_claim_sources=True,
            )
        )
        dialogue_path = article_path.with_name(
            f"{issue_date}-DeepDive-dialogue.md"
        )
        if _history_regular_file(dialogue_path, repo_root=repo):
            dialogue_paths.append(dialogue_path)

    dialogue_corpus_audit = deepdive_dialogue.audit_dialogue_corpus(
        dialogue_paths,
        repo_root=repo,
    )
    issue_codes = sorted(
        {
            str(code)
            for row in rows
            for code in row.get("issueCodes", [])
        }
    )
    if dialogue_corpus_audit.get("issues"):
        issue_codes = sorted(
            {*issue_codes, "deepdive_dialogue_value_invalid"}
        )
    return {
        "schemaVersion": HISTORY_REPORT_SCHEMA,
        "status": "Green" if not issue_codes else "Red",
        "startDate": start_date,
        "endDate": end_date,
        "route": route,
        "issueCodes": issue_codes,
        "articleCount": len(rows),
        "days": rows,
        "dialogueCorpusAudit": dialogue_corpus_audit,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--article", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--timeout", type=float, default=20.0)
    capture_period = subparsers.add_parser("capture-period")
    capture_period.add_argument("--start", required=True)
    capture_period.add_argument("--end", required=True)
    capture_period.add_argument("--timeout", type=float, default=20.0)
    materialize = subparsers.add_parser("materialize-issue")
    materialize.add_argument("--date", required=True)
    materialize.add_argument("--timeout", type=float, default=20.0)
    materialize.add_argument("--context-pack", type=Path)
    materialize.add_argument("--render-public", action="store_true")
    audit = subparsers.add_parser("audit-issue")
    audit.add_argument("--date", required=True)
    audit.add_argument("--require-rendered-public", action="store_true")
    audit.add_argument("--route", default="codex_daily_audit")
    period = subparsers.add_parser("audit-period")
    period.add_argument("--start", required=True)
    period.add_argument("--end", required=True)
    period.add_argument("--require-rendered-public", action="store_true")
    period.add_argument("--route", default="codex_daily_audit")
    history = subparsers.add_parser("audit-history")
    history.add_argument("--start", required=True)
    history.add_argument("--end", required=True)
    history.add_argument("--require-rendered-public", action="store_true")
    history.add_argument("--route", default="codex_daily_audit")
    history.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            result = capture_provenance(
                article_path=args.article,
                output_path=args.output,
                timeout=args.timeout,
            )
            status = "Green"
        elif args.command == "capture-period":
            result = capture_period_provenance(
                repo_root=args.repo_root,
                start_date=args.start,
                end_date=args.end,
                timeout=args.timeout,
            )
            status = result["status"]
        elif args.command == "materialize-issue":
            result = materialize_issue_bundle(
                repo_root=args.repo_root,
                issue_date=args.date,
                timeout=args.timeout,
                context_pack_path=args.context_pack,
                render_public=args.render_public,
            )
            status = result["status"]
        elif args.command == "audit-issue":
            result = audit_issue(
                repo_root=args.repo_root,
                issue_date=args.date,
                require_rendered_public=args.require_rendered_public,
                route=args.route,
            )
            status = result["status"]
        elif args.command == "audit-period":
            result = audit_period(
                repo_root=args.repo_root,
                start_date=args.start,
                end_date=args.end,
                require_rendered_public=args.require_rendered_public,
                route=args.route,
            )
            status = result["status"]
        elif args.command == "audit-history":
            history_output = None
            if args.output is not None:
                history_output = _validate_history_output_path(
                    args.output,
                    repo_root=Path(args.repo_root).resolve(),
                )
            result = audit_history(
                repo_root=args.repo_root,
                start_date=args.start,
                end_date=args.end,
                require_rendered_public=args.require_rendered_public,
                route=args.route,
            )
            if history_output is not None:
                history_output = _validate_history_output_path(
                    args.output,
                    repo_root=Path(args.repo_root).resolve(),
                )
                if isinstance(result, dict):
                    json_result = json.loads(
                        json.dumps(result, ensure_ascii=False)
                    )
                    result.clear()
                    result.update(json_result)
                _atomic_write_json(history_output, result)
            status = result["status"]
        else:
            raise DeepDiveQualityError("DEEPDIVE_COMMAND_UNKNOWN")
    except (DeepDiveQualityError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 3 if "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID" in str(error) else 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if status == "Green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
