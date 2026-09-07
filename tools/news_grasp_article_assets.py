"""採用記事だけの画像実体を検査し、観測できたOGPへ限定して補正する。"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


class AssetQualityError(ValueError):
    """画像ではない、または画像取得先の品質条件を満たさない。"""

    def __init__(self, reason: str, *, prepared_payload=None, mutation_paths=None):
        super().__init__(reason)
        self.prepared_payload = prepared_payload
        self.mutation_paths = mutation_paths


class AssetObservationPending(RuntimeError):
    """通信結果を確定できず、モデルの再送が解決にならない。"""


def validate_public_url(url: str) -> None:
    from tools.safe_public_fetch import validate_public_http_url
    try:
        host = (urlparse(url).hostname or '').rstrip('.').casefold()
        if host.endswith(('.invalid', '.example')):
            raise ValueError('image_url_invalid')
        validate_public_http_url(url)
    except (TypeError, ValueError) as exc:
        if str(exc) == 'public_fetch_dns_unverified':
            raise AssetObservationPending('image_dns_pending') from exc
        raise AssetQualityError('image_url_invalid') from exc


def verify_image_bytes(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise AssetQualityError('image_bytes_limit')
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            if picture.width * picture.height > MAX_IMAGE_PIXELS:
                raise AssetQualityError('image_pixels_limit')
            result = {'format': picture.format, 'width': picture.width, 'height': picture.height}
            picture.verify()
        with Image.open(io.BytesIO(raw)) as picture:
            picture.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise AssetQualityError('image_decode') from exc
    return {**result, 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_count': len(raw)}


def _read_public_bytes(url: str) -> tuple[bytes, str, str]:
    from tools.safe_public_fetch import safe_urlopen
    deadline = time.monotonic() + 8
    validate_public_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 News-Grasp image verification'})
    try:
        with safe_urlopen(request, timeout=8, deadline=deadline) as response:
            chunks = []
            count = 0
            while count <= MAX_IMAGE_BYTES:
                if time.monotonic() >= deadline:
                    raise AssetObservationPending('image_timeout')
                stream_socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if stream_socket is not None:
                    stream_socket.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = response.read1(min(65536, MAX_IMAGE_BYTES + 1 - count))
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
            raw = b''.join(chunks)
            observed_url = response.geturl()
            content_type = response.headers.get('Content-Type', '')
        if len(raw) > MAX_IMAGE_BYTES:
            raise AssetQualityError('image_bytes_limit')
        return raw, observed_url, content_type
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise AssetQualityError('image_not_found') from exc
        raise AssetObservationPending(f'image_http_{exc.code}') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssetObservationPending('image_network_pending') from exc
    except ValueError as exc:
        if isinstance(exc, AssetQualityError):
            raise
        if str(exc) == 'public_fetch_dns_unverified':
            raise AssetObservationPending('image_dns_pending') from exc
        raise AssetQualityError('image_url_invalid') from exc


def probe_image(url: str) -> dict[str, Any]:
    raw, observed_url, _ = _read_public_bytes(url)
    return {**verify_image_bytes(raw), 'observed_url': observed_url}


def publication_date_from_html(html: str, article_url: str) -> str | None:
    """記事の初出メタ情報だけをJSTへ変換し、更新日や本文の日付を混ぜない。"""
    class PublishedParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.values, self.scripts, self.current_script = [], [], None

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            name = str(values.get('property') or values.get('name') or values.get('itemprop') or '').casefold()
            if name in {'article:published_time', 'og:published_time', 'datepublished', 'pubdate', 'publishdate', 'dc.date.issued'}:
                self.values.append(values.get('content') or values.get('datetime'))
            if tag == 'script' and str(values.get('type') or '').casefold() == 'application/ld+json':
                self.current_script = []

        def handle_data(self, data):
            if self.current_script is not None:
                self.current_script.append(data)

        def handle_endtag(self, tag):
            if tag == 'script' and self.current_script is not None:
                self.scripts.append(''.join(self.current_script))
                self.current_script = None

    parser = PublishedParser()
    parser.feed(html)
    def identity(url):
        parsed = urlparse(str(url))
        return parsed.netloc.casefold(), parsed.path.rstrip('/')

    visited = 0
    def collect(value, depth=0):
        nonlocal visited
        visited += 1
        if depth > 32 or visited > 2048:
            raise ValueError('source_json_ld_limit')
        if isinstance(value, list):
            return [item for node in value for item in collect(node, depth + 1)]
        if not isinstance(value, dict):
            return []
        kinds = value.get('@type', [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        canonical = value.get('url') or value.get('mainEntityOfPage')
        if isinstance(canonical, dict):
            canonical = canonical.get('@id')
        if (isinstance(kinds, list) and any(str(kind).rsplit('/', 1)[-1] in
                {'NewsArticle', 'Article', 'BlogPosting', 'Report', 'LiveBlogPosting'} for kind in kinds)
                and (not canonical or identity(canonical) == identity(article_url))
                and value.get('datePublished')):
            return [value['datePublished']]
        return collect(value.get('@graph', []), depth + 1)

    values = list(parser.values)
    if not values:
        for script in parser.scripts:
            try:
                if len(script.encode('utf-8')) > 256 * 1024:
                    return None
                values.extend(collect(json.loads(script)))
            except (ValueError, TypeError, RecursionError):
                continue
    normalized = set()
    for value in values:
        try:
            moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if moment.tzinfo is not None:
                moment = moment.astimezone(timezone(timedelta(hours=9)))
            normalized.add(moment.date().isoformat())
        except ValueError:
            return None
    return next(iter(normalized)) if len(normalized) == 1 else None


def _fetch_public_ogp(url: str, **_: Any) -> dict[str, Any]:
    from tools.fetch_ogp import _OGPParser, _absolutize, _decode_html

    raw, observed_url, content_type = _read_public_bytes(url)
    html = _decode_html(raw, content_type)
    parser = _OGPParser()
    parser.feed_until_stop(html)
    return {'status': 'ok', 'source_url': observed_url, 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'published_date': publication_date_from_html(html, observed_url),
            'og_image': _absolutize(observed_url, parser.og_image),
            'twitter_image': _absolutize(observed_url, parser.twitter_image)}


def _replace_card_image(digest: str, record: Mapping[str, Any], image_url: str) -> str:
    from tools.generate_pages import parse_articles

    parts = re.split(r'(\r?\n---\r?\n)', digest)
    matches = []
    for index, part in enumerate(parts):
        cards = parse_articles(part)
        if len(cards) != 1:
            continue
        card = cards[0]
        if card['source_url'] == record.get('url') or (
            not card['source_url'] and card['title'] in {record.get('title'), record.get('title_ja')}
        ):
            matches.append(index)
    if len(matches) != 1:
        return digest
    index = matches[0]
    image_line = f'![thumb]({image_url})'
    block = parts[index]
    pattern = r'(?m)^!?\[thumb\]\([^\r\n]*\)[ \t]*$'
    if re.search(pattern, block):
        block = re.sub(pattern, lambda _: image_line, block, count=1)
    else:
        block = re.sub(r'(?m)^(### \[[^\]]+\][^\r\n]*)$',
                       lambda m: m.group(1) + '\n\n' + image_line, block, count=1)
    parts[index] = block
    return ''.join(parts)


def prepare_reporter_assets(
    payload: Mapping[str, Any], *,
    issue_date: str | None = None,
    probe: Callable[[str], dict[str, Any]] = probe_image,
    fetch_ogp_func: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    records = result.get('records')
    if not isinstance(records, list) or not 1 <= len(records) <= 5 or not isinstance(result.get('digest_markdown'), str):
        return result
    evidence = []
    source_evidence_rows = []
    source_failures = []
    failures = []
    fetch = fetch_ogp_func or _fetch_public_ogp
    for index, record in enumerate(records):
        if (not isinstance(record, dict)
                or not all(isinstance(record.get(key), str) for key in ('url', 'title', 'title_ja'))
                or not str(record.get('url')).startswith(('https://', 'http://'))):
            continue
        original = str(record.get('thumb') or '')
        thumb = original
        source = 'original_image'
        source_evidence = {}
        ogp = None
        if issue_date is not None:
            try:
                ogp = fetch(record['url'], timeout=8, retries=0)
            except AssetQualityError:
                source_failures.append(str(index))
                continue
            if ogp.get('status') in {'timeout', 'url_error', 'fetch_error'} or str(ogp.get('status')).startswith('http_5'):
                raise AssetObservationPending('article_source_pending')
            observed_source = {key: ogp.get(key) for key in ('source_url', 'source_sha256', 'published_date')}
            observed_source['record_index'] = index
            observed_source['article_url'] = record['url']
            source_evidence_rows.append(observed_source)
            if ogp.get('published_date') != issue_date:
                source_failures.append(str(index))
                continue
        try:
            observed = probe(thumb)
        except AssetQualityError:
            article_url = str(record.get('url') or '')
            try:
                ogp = ogp or fetch(article_url, timeout=8, retries=0)
            except AssetQualityError:
                failures.append(str(index))
                continue
            if ogp.get('status') in {'timeout', 'url_error', 'fetch_error'} or str(ogp.get('status')).startswith('http_5'):
                raise AssetObservationPending('article_ogp_pending')
            observed = None
            for candidate in dict.fromkeys([ogp.get('og_image'), ogp.get('twitter_image')]):
                if not isinstance(candidate, str) or not candidate:
                    continue
                try:
                    observed = probe(candidate)
                    thumb = candidate
                    source = 'source_ogp'
                    source_evidence = {key: ogp[key] for key in ('source_url', 'source_sha256') if key in ogp}
                    break
                except AssetQualityError:
                    continue
            if observed is None:
                failures.append(str(index))
                continue
        record['thumb'] = thumb
        result['digest_markdown'] = _replace_card_image(result['digest_markdown'], record, thumb)
        evidence.append({'record_index': index, 'article_url': record.get('url'),
                         'original_thumb': original, 'image_url': thumb, 'source': source,
                         **source_evidence, **observed})
    if not isinstance(result.get('search_audit'), dict):
        return result
    result['search_audit']['thumbnail_evidence'] = evidence
    if issue_date is not None:
        result['search_audit']['source_evidence'] = source_evidence_rows
    if failures or source_failures:
        reason = ('source_publication_invalid:' + ','.join(source_failures) if source_failures
                  else 'thumbnail_unrepairable:' + ','.join(failures))
        raise AssetQualityError(reason, prepared_payload=result, mutation_paths=[
            '/digest_markdown', *[f'/records/{index}' for index in source_failures],
            *[f'/records/{index}/thumb' for index in failures]])
    return result
