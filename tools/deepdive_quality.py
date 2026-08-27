"""DeepDive URL provenanceと対談価値を一つの境界で検証する。"""

from __future__ import annotations

import argparse
import hashlib
import json
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

from tools.tts import deepdive_dialogue, proc
from tools.validate_deepdive_urls import extract_urls


LEGACY_SCHEMA = "DEEPDIVE_SOURCE_PROVENANCE_V1"
SCHEMA = "DEEPDIVE_SOURCE_PROVENANCE_V2"
REPORT_SCHEMA = "DEEPDIVE_SHARED_QUALITY_REPORT_V1"
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


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
                field: observations[url][field]
                for field in cache_fields
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
            or len(_normalized_evidence_text(row["evidence"])) < 12
        ):
            raise DeepDiveQualityError(
                "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID binding_value"
            )
        seen_ids.add(claim_id)
        declarations.append(row)
    return declarations


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
            "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID bindings_missing"
        )
    bindings: list[dict[str, str]] = []
    for row in declarations:
        record = observed.get(row["sourceUrl"])
        observed_text = str((record or {}).get("observedText") or "")
        evidence = _normalized_evidence_text(row["evidence"])
        if not record or not observed_text or evidence not in _normalized_evidence_text(observed_text):
            raise DeepDiveQualityError(
                "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID "
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
    for row in records:
        if (
            not isinstance(row, dict)
            or not required.issubset(row)
            or set(row) - required != ({"observedText"} if "observedText" in row else set())
            or ("observedText" in row and not isinstance(row["observedText"], str))
        ):
            raise DeepDiveQualityError("DEEPDIVE_FETCH_RECORD_INVALID")
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
) -> dict[str, Any]:
    """実取得記録を記事内URLの全出現位置へ束縛する。"""

    try:
        article = Path(article_path).resolve(strict=True)
    except OSError as error:
        raise DeepDiveQualityError("DEEPDIVE_ARTICLE_MISSING") from error
    text = article.read_text(encoding="utf-8-sig")
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
        "articleSha256": _canonical_text_sha256(article.read_bytes()),
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
    issues = [
        f"DEEPDIVE_RENDERED_PUBLIC_HREF_MISSING {href}"
        for href in sorted(required_hrefs - collector.hrefs)
    ]
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
) -> list[str]:
    """生成時に実取得本文へ照合したclaim bindingを記事とmanifestへ再束縛する。"""

    article = Path(article_path).resolve()
    manifest = Path(manifest_path).resolve()
    if not article.is_file() or not manifest.is_file():
        return []
    try:
        article_text = article.read_text(encoding="utf-8-sig")
        value = json.loads(manifest.read_text(encoding="utf-8-sig"))
        issue_day = date.fromisoformat(_issue_date(article))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return ["DEEPDIVE_CLAIM_SOURCE_FIT_INVALID"]
    if not isinstance(value, dict):
        return ["DEEPDIVE_CLAIM_SOURCE_FIT_INVALID"]
    schema = value.get("schemaVersion")
    if schema == LEGACY_SCHEMA:
        return (
            ["DEEPDIVE_CLAIM_SOURCE_MANIFEST_LEGACY"]
            if issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE
            else []
        )
    if schema != SCHEMA:
        return ["DEEPDIVE_CLAIM_SOURCE_SCHEMA_INVALID"]
    try:
        expected = sorted(
            (_claim_binding_fingerprint(row) for row in _claim_source_declarations(article_text)),
            key=lambda item: item["claimId"],
        )
    except DeepDiveQualityError as error:
        return [str(error)]
    if issue_day >= CLAIM_SOURCE_ENFORCEMENT_DATE and not expected:
        return ["DEEPDIVE_CLAIM_SOURCE_BINDINGS_MISSING"]
    actual = value.get("claimBindings")
    issues: list[str] = []
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
    if set(value) != required_fields or schema not in {LEGACY_SCHEMA, SCHEMA}:
        return ["DEEPDIVE_PROVENANCE_SCHEMA_INVALID"], evidence
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
    }


def _run_system_curl(
    url: str,
    *,
    timeout: float,
) -> tuple[int, str, bytes]:
    """Python transportが限定的に拒否された時だけWindows curlで一回取得する。"""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="news-grasp-provenance-",
        suffix=".body",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        result = proc.quiet_run(
            [
                "curl.exe",
                "--location",
                "--max-redirs",
                "10",
                "--max-time",
                str(max(1, int(timeout))),
                "--silent",
                "--show-error",
                "--fail-with-body",
                "--range",
                f"0-{MAX_OBSERVED_BYTES - 1}",
                "--max-filesize",
                str(MAX_OBSERVED_BYTES),
                "--user-agent",
                USER_AGENT,
                "--header",
                "Accept: text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
                "--header",
                "Accept-Language: ja,en-US;q=0.9,en;q=0.8",
                "--output",
                str(temporary),
                "--write-out",
                "%{http_code}\t%{url_effective}",
                url,
            ],
            timeout=timeout + 3,
            check=False,
        )
        metadata = (result.stdout or "").strip().split("\t", maxsplit=1)
        if result.returncode != 0 or len(metadata) != 2 or not metadata[0].isdigit():
            detail = (result.stderr or result.stdout or "").strip()
            raise DeepDiveQualityError(
                f"DEEPDIVE_SYSTEM_FETCH_FAILED {url} exit={result.returncode} {detail}"
            )
        return int(metadata[0]), metadata[1], temporary.read_bytes()
    finally:
        temporary.unlink(missing_ok=True)


def _fetch_one_system(url: str, *, timeout: float) -> dict[str, object]:
    status, final_url, body = _run_system_curl(url, timeout=timeout)
    return _observed_record(
        url=url,
        final_url=final_url,
        status=status,
        body=body,
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
        if error.code in (403, 405, 429, 501) or 300 <= error.code < 400:
            return _fetch_one_system(url, timeout=timeout)
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} HTTPError: {error}"
        ) from error
    except URLError as error:
        if "CERTIFICATE_VERIFY_FAILED" in str(error):
            return _fetch_one_system(url, timeout=timeout)
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} {type(error).__name__}: {error}"
        ) from error
    except TimeoutError:
        return _fetch_one_system(url, timeout=timeout)
    except OSError as error:
        raise DeepDiveQualityError(
            f"DEEPDIVE_URL_FETCH_FAILED {url} {type(error).__name__}: {error}"
        ) from error
    return _observed_record(
        url=url,
        final_url=final_url,
        status=status,
        body=bytes(body),
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


def audit_issue(
    *,
    repo_root: Path,
    issue_date: str,
    include_corpus: bool = True,
    require_rendered_public: bool = False,
) -> dict[str, Any]:
    """production runnerと日次監査が共有する一日分の品質判定。"""

    issue_day = date.fromisoformat(issue_date)
    repo = Path(repo_root).resolve()
    article = repo / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    dialogue = repo / "digest" / "DeepDive" / f"{issue_date}-DeepDive-dialogue.md"
    manifest = repo / "data" / "deepdive-provenance" / f"{issue_date}.json"
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
    claim_source_issues = validate_claim_source_fit(article, manifest)
    if claim_source_issues:
        issue_codes.append("deepdive_claim_source_fit_invalid")
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
    dialogue_corpus_audit: dict[str, object] | None = None
    audited_files = {
        row["path"]: row for row in provenance_evidence
    }
    for row in rendered_evidence:
        audited_files[row["path"]] = row
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
        "issueCodes": issue_codes,
        "issues": issues,
        "articlePath": str(article),
        "dialoguePath": str(dialogue),
        "provenancePath": str(manifest),
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
) -> dict[str, Any]:
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
        "issueCodes": issue_codes,
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
    audit = subparsers.add_parser("audit-issue")
    audit.add_argument("--date", required=True)
    audit.add_argument("--require-rendered-public", action="store_true")
    period = subparsers.add_parser("audit-period")
    period.add_argument("--start", required=True)
    period.add_argument("--end", required=True)
    period.add_argument("--require-rendered-public", action="store_true")
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
        elif args.command == "audit-issue":
            result = audit_issue(
                repo_root=args.repo_root,
                issue_date=args.date,
                require_rendered_public=args.require_rendered_public,
            )
            status = result["status"]
        else:
            result = audit_period(
                repo_root=args.repo_root,
                start_date=args.start,
                end_date=args.end,
                require_rendered_public=args.require_rendered_public,
            )
            status = result["status"]
    except (DeepDiveQualityError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 3 if "DEEPDIVE_CLAIM_SOURCE_FIT_INVALID" in str(error) else 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if status == "Green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
