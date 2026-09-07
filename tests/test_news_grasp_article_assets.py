"""画像の実体検査と本文を変えないOGP修復。"""
from __future__ import annotations

import copy
import importlib
import io

import pytest
from PIL import Image


def _module():
    return importlib.import_module('tools.news_grasp_article_assets')


def _image():
    out = io.BytesIO()
    Image.new('RGB', (32, 24), 'navy').save(out, format='PNG')
    return out.getvalue()


def _payload():
    from tests.test_news_grasp_daily_card_contract import _payload as payload
    return payload()


def test_http_200_html_is_not_image():
    m = _module()
    with pytest.raises(m.AssetQualityError, match='image_decode'):
        m.verify_image_bytes(b'<html>article</html>')
    assert m.verify_image_bytes(_image())['format'] == 'PNG'


def test_valid_image_does_not_trigger_ogp_or_change_content():
    m = _module()
    payload = _payload()
    result = m.prepare_reporter_assets(payload,
        probe=lambda _: m.verify_image_bytes(_image()),
        fetch_ogp_func=lambda *_a, **_k: pytest.fail('正常画像のOGP再取得'))
    assert result['records'] == payload['records']
    assert result['digest_markdown'] == payload['digest_markdown']
    assert result['search_audit']['thumbnail_evidence'][0]['source'] == 'original_image'


def test_html_thumbnail_is_repaired_only_in_matching_card():
    m = _module()
    payload = _payload()
    before = copy.deepcopy(payload)
    old = payload['records'][0]['thumb']
    new = 'https://example.com/verified.png'
    def probe(url):
        if url == old:
            raise m.AssetQualityError('image_decode')
        assert url == new
        return m.verify_image_bytes(_image())
    result = m.prepare_reporter_assets(payload, probe=probe,
        fetch_ogp_func=lambda *_a, **_k: {'status':'ok', 'og_image':new, 'twitter_image':None})
    assert payload == before
    assert result['records'][0] == {**payload['records'][0], 'thumb':new}
    assert result['digest_markdown'] == payload['digest_markdown'].replace(
        f'![thumb]({old})', f'![thumb]({new})')


def test_failed_observation_is_pending_and_keeps_raw_payload():
    m = _module()
    payload = _payload()
    before = copy.deepcopy(payload)
    def probe(_):
        raise m.AssetObservationPending('network_timeout')
    with pytest.raises(m.AssetObservationPending):
        m.prepare_reporter_assets(payload, probe=probe,
            fetch_ogp_func=lambda *_a, **_k: pytest.fail('送信不明後の余分な取得'))
    assert payload == before


def test_invalid_ogp_cannot_be_promoted_to_image():
    m = _module()
    def probe(_):
        raise m.AssetQualityError('image_decode')
    with pytest.raises(m.AssetQualityError, match='thumbnail_unrepairable:0'):
        m.prepare_reporter_assets(_payload(), probe=probe,
            fetch_ogp_func=lambda *_a, **_k: {'status':'ok', 'og_image':'https://example.com/html'})


@pytest.mark.parametrize('url', ['file:///C:/secret', 'http://127.0.0.1/image',
    'http://[::1]/image', 'https://user:pass@example.com/image',
    'http://localhost/image', 'https://example.invalid/image'])
def test_non_public_url_is_rejected_before_fetch(url):
    m = _module()
    with pytest.raises(m.AssetQualityError, match='image_url'):
        m.validate_public_url(url)


def test_missing_image_markup_is_repaired_without_rewriting_bullets():
    m = _module()
    payload = _payload()
    payload['digest_markdown'] = payload['digest_markdown'].replace('![thumb]', '[thumb]')
    result = m.prepare_reporter_assets(payload, probe=lambda _: m.verify_image_bytes(_image()),
        fetch_ogp_func=lambda *_a, **_k: pytest.fail('正常画像のOGP再取得'))
    from tools.generate_pages import parse_articles
    card, = parse_articles(result['digest_markdown'])
    assert card['thumb'] == payload['records'][0]['thumb']
    assert result['records'] == payload['records']


def test_redirect_to_private_address_is_rejected():
    import urllib.request
    from tools import safe_public_fetch
    with pytest.raises(ValueError, match='public_fetch'):
        safe_public_fetch._SafeRedirectHandler().redirect_request(urllib.request.Request('https://example.com/image'),
            None, 302, 'Found', {}, 'http://127.0.0.1/internal')


def test_public_hostname_resolving_to_private_address_is_rejected(monkeypatch):
    m = _module()
    from tools import safe_public_fetch
    monkeypatch.setattr(safe_public_fetch.socket, 'getaddrinfo', lambda *_a, **_k: [(2, 1, 6, '', ('10.0.0.1', 443))])
    with pytest.raises(m.AssetQualityError, match='image_url'):
        m.validate_public_url('https://example.com/image')


def test_image_transport_uses_shared_pinned_fetch_boundary(monkeypatch):
    m = _module()
    from tools import safe_public_fetch
    monkeypatch.setattr(m, 'validate_public_url', lambda _: None)
    monkeypatch.setattr(m.urllib.request, 'build_opener', lambda *_: pytest.fail('共有transportを迂回'))
    def reject(*_a, **_k):
        raise ValueError('public_fetch_address_forbidden')
    monkeypatch.setattr(safe_public_fetch, 'safe_urlopen', reject)
    with pytest.raises(m.AssetQualityError, match='image_url'):
        m._read_public_bytes('https://example.com/image')


def test_pending_image_does_not_become_model_quality_failure(monkeypatch):
    from tools import news_grasp_daily_content as content
    m = _module()
    def pending(_):
        raise m.AssetObservationPending('image_network_pending')
    monkeypatch.setattr(m, 'prepare_reporter_assets', pending)
    with pytest.raises(content.ModelResultPending, match='reporter_assets'):
        content._prepare_reporter_assets(_payload(), category='ai')


def test_image_repair_preserves_body_references_and_other_cards():
    m = _module()
    payload = _payload()
    old = payload['records'][0]['thumb']
    reference = f'本文中の参照先 {old} は画像欄の置換対象ではない。'
    payload['digest_markdown'] += '\n' + reference
    second = copy.deepcopy(payload['records'][0])
    second.update(title='別記事', title_ja='別記事', url='https://example.com/other',
                  thumb='https://example.com/other.png')
    payload['records'].append(second)
    other_card = f"### [87] 別記事\n\n🔗 [元記事]({second['url']})\n\n![thumb]({second['thumb']})\n"
    payload['digest_markdown'] += '\n---\n' + other_card
    def probe(url):
        if url == old:
            raise m.AssetQualityError('image_decode')
        return m.verify_image_bytes(_image())
    result = m.prepare_reporter_assets(payload, probe=probe,
        fetch_ogp_func=lambda *_a, **_k: {'status':'ok', 'og_image':'https://example.com/new.png'})
    assert reference in result['digest_markdown']
    assert result['digest_markdown'].endswith(other_card)
    assert result['records'][1] == second


@pytest.mark.parametrize('html,expected', [
    ('<meta property="article:published_time" content="2026-09-05T18:30:00Z">', '2026-09-06'),
    ('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-09-05T07:00:00+09:00","dateModified":"2026-09-06T07:00:00+09:00"}</script>', '2026-09-05'),
    ('<meta property="article:published_time" content="2026-09-05"><meta property="article:published_time" content="2026-09-06">', None),
    ('<p>関連ニュース 2026-09-05</p>', None),
])
def test_publication_date_uses_publisher_metadata_not_modified_or_body(html, expected):
    assert _module().publication_date_from_html(html, 'https://example.com/article') == expected


def test_wrong_issue_source_is_not_accepted_with_valid_image():
    m = _module()
    payload = _payload()
    with pytest.raises(m.AssetQualityError, match='source_publication_invalid:0') as failure:
        m.prepare_reporter_assets(payload, issue_date=payload['issue_date'],
            probe=lambda _: m.verify_image_bytes(_image()),
            fetch_ogp_func=lambda *_a, **_k: {'status':'ok', 'published_date':'2026-09-06',
                'source_url':payload['records'][0]['url'], 'source_sha256':'a'*64})
    assert failure.value.prepared_payload['search_audit']['source_evidence'][0]['published_date'] == '2026-09-06'


def test_publication_failure_and_card_failures_share_one_scoped_repair(monkeypatch):
    from tools import news_grasp_daily_content as content
    m = _module()
    payload = _payload()
    del payload['records'][0]['score']
    def invalid_source(raw, **_):
        raise m.AssetQualityError('source_publication_invalid:0', prepared_payload=raw)
    monkeypatch.setattr(m, 'prepare_reporter_assets', invalid_source)
    with pytest.raises(content.DailyContentError) as failure:
        content._prepare_reporter_assets(payload, category='ai', issue_date=payload['issue_date'])
    assert set(content._repair_allowed_paths(artifact_id='reporter:ai',
        reason_code=str(failure.value), invalid_payload=payload)) == {'/records/0', '/digest_markdown'}
