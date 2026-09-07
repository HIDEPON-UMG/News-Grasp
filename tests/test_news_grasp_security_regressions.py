"""独立監査で見つかった入力境界と保存修復の回帰。"""
from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize('fields', ['dialogue_markdown', 'article_markdown', 'article_markdown,dialogue_markdown'])
def test_review_failure_keeps_scope_after_reason_normalization(fields):
    from tools import news_grasp_daily_content as c
    failure = c._failure_checkpoint_value(run_id='review-fixture', issue_date='2026-09-05',
        stage='deepdive_evidence', artifact_id='deepdive_model', predicate_id='deepdive_value',
        reason_code=f'DEEPDIVE_OUTPUT_INVALID:review:{fields}|具体的所見|補足',
        input_hash='a'*64, cause_input_mask=('deepdive_model',),
        invalid_payload={'article_markdown':'元の記事','dialogue_markdown':'元の対談'})
    assert failure['allowedMutationPaths'] == ['/'+field for field in fields.split(',')]
    changed = dict(failure['invalidPayload'])
    for field in fields.split(','):
        changed[field] = '修正した内容'
    c._assert_repair_scope(failure, changed)


def test_path_validator_uses_supplied_bounded_bytes(tmp_path, monkeypatch):
    from tools import deepdive_quality as q
    article = tmp_path / '2026-09-05-DeepDive.md'
    manifest = tmp_path / 'provenance.json'
    article.write_text('placeholder', encoding='utf-8')
    manifest.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(type(article), 'read_bytes', lambda _: pytest.fail('path再読込'))
    issues, _ = q._validate_provenance_with_evidence(article, manifest,
        article_bytes_override=b'---\ndate: 2026-09-05\n---\n', manifest_bytes_override=b'{}')
    assert 'DEEPDIVE_PROVENANCE_SCHEMA_INVALID' in issues


def test_deep_json_ld_is_typed_unverified_source():
    from tools.news_grasp_article_assets import publication_date_from_html
    html = '<script type="application/ld+json">' + '['*1500 + '{}' + ']'*1500 + '</script>'
    assert publication_date_from_html(html, 'https://example.com/article') is None


def test_shared_fetch_refuses_expired_deadline_before_network(monkeypatch):
    from tools import safe_public_fetch as f
    monkeypatch.setattr(f.time, 'monotonic', lambda: 10.0)
    monkeypatch.setattr(f, 'validate_public_http_url', lambda _: pytest.fail('期限後のDNS/HTTP'))
    with pytest.raises(TimeoutError, match='deadline'):
        f.safe_urlopen('https://example.com/article', timeout=8, deadline=9.0)


def test_deepdive_image_rebind_does_not_rewrite_prose():
    from tools import news_grasp_daily_content as c
    from tests.test_news_grasp_daily_content import _deepdive, _record
    original = _deepdive()
    old = _record('ai')
    new = {**old, 'thumb':'https://example.com/ai/new.jpg'}
    rebound = c._rebind_deepdive_images(original, [old], [new])
    assert rebound['article_markdown'] == original['article_markdown'].replace(
        "og_image: 'https://example.com/ai/image.jpg'", "og_image: 'https://example.com/ai/new.jpg'")
    assert rebound['dialogue_markdown'] == original['dialogue_markdown']
