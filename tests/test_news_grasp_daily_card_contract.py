"""Reporterの記録と実rendererが読むカードを一致させる境界。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import news_grasp_daily_content as content


def _payload():
    from tests.test_news_grasp_daily_content import _record, ISSUE_DATE
    record = _record('ai')
    record['score'] = 87
    record['bullets'] = ['【事実・概要】：製品が公開された。',
                         '【背景・要点】：既存の運用条件を確認する。',
                         '【影響・展望】：導入判断には性能検証が必要となる。']
    digest = (f"### [87] {record['title_ja']}\n\n"
              f"📅 {ISSUE_DATE} | 📰 {record['source']} | 🔗 [元記事]({record['url']})\n\n"
              f"![thumb]({record['thumb']})\n\n" +
              '\n'.join('- ' + value for value in record['bullets']) + '\n')
    audit = {'queries': ['ai'], 'raw_results_total': 1,
             'candidates_total': 1, 'selected_total': 1}
    return {'category': 'ai', 'issue_date': ISSUE_DATE, 'records': [record],
            'digest_markdown': digest, 'search_audit': audit}


def _validate(payload):
    return content._validate_reporter(payload, category='ai',
        issue_date=payload['issue_date'], search_audit=payload['search_audit'])


def test_real_renderer_matches_validated_record():
    from tools.generate_pages import parse_articles, inline_html
    result = _validate(_payload())
    card, = parse_articles(result['digest_markdown'])
    record, = result['records']
    assert card['score'] == str(record['score'])
    assert card['source_url'] == record['url']
    assert card['thumb'] == record['thumb']
    assert card['bullets'] == [inline_html(v) for v in record['bullets']]


@pytest.mark.parametrize('old,new', [('[87]', '[1]'),
    ('[元記事](https://example.com/ai/article)', '[元記事](https://example.com/ai/other)'),
    ('![thumb]', '[thumb]'), ('- 【背景・要点】', '【背景・要点】'),
    ('製品が公開された。', '保存記録にない内容。')])
def test_display_mismatch_is_rejected_without_changing_record(old, new):
    payload = _payload()
    payload['digest_markdown'] = payload['digest_markdown'].replace(old, new)
    before = copy.deepcopy(payload)
    with pytest.raises(content.DailyContentError, match='card_contract') as failure:
        _validate(payload)
    assert payload == before
    assert content._repair_allowed_paths(artifact_id='reporter:ai',
        reason_code=str(failure.value), invalid_payload=payload) == ['/digest_markdown']


@pytest.mark.parametrize('score', [None, True, -1, 101, '87', 87.0])
def test_missing_or_invalid_score_has_field_scoped_recovery(score):
    payload = _payload()
    if score is None:
        del payload['records'][0]['score']
    else:
        payload['records'][0]['score'] = score
    with pytest.raises(content.DailyContentError, match='card_contract') as failure:
        _validate(payload)
    paths = content._repair_allowed_paths(artifact_id='reporter:ai',
        reason_code=str(failure.value), invalid_payload=payload)
    assert set(paths) == {'/records/0/score', '/digest_markdown'}


def test_multiple_broken_records_are_one_repair_scope():
    payload = _payload()
    payload['records'].append(copy.deepcopy(payload['records'][0]))
    payload['digest_markdown'] += '\n---\n' + payload['digest_markdown']
    del payload['records'][0]['score']
    payload['records'][1]['bullets'] = ['内容不足']
    with pytest.raises(content.DailyContentError, match='card_contract') as failure:
        _validate(payload)
    assert set(content._repair_allowed_paths(artifact_id='reporter:ai',
        reason_code=str(failure.value), invalid_payload=payload)) == {
            '/records/0/score', '/records/1/bullets', '/digest_markdown'}


def test_all_daily_record_schemas_require_the_existing_score_contract():
    root = Path(__file__).resolve().parents[1]
    for name in ('reporter', 'reporter_shard', 'editor'):
        schema = json.loads((root / 'schemas' /
            f'news_grasp_daily_{name}_output.schema.json').read_text())
        if name == 'reporter_shard':
            schema = schema['properties']['reporters']['items']
        key = 'append_records' if name == 'editor' else 'records'
        record = schema['properties'][key]['items']
        assert 'score' in record['required']
        assert record['properties']['score'] == {'type': 'integer', 'minimum': 0, 'maximum': 100}


def test_old_completion_revalidates_saved_cards_without_model_call(tmp_path, monkeypatch):
    from tests.test_news_grasp_daily_content import (
        _candidate_provider, _model_runner, ISSUE_DATE, RUN_ID)
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/articles.jsonl').write_text('', encoding='utf-8')
    args = dict(repo_root=tmp_path, issue_date=ISSUE_DATE, run_id=RUN_ID,
        scheduled_categories=('fx', 'ai'), candidate_provider=_candidate_provider,
        model_runner=_model_runner,
        derived_builder=lambda **_: {'ok': True, 'status': 'built', 'artifacts': []})
    content.produce_current_issue(**args)
    path = tmp_path / 'build/daily-content' / RUN_ID / 'completion.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved.pop('dailyCardContractVersion', None)
    path.write_text(json.dumps(saved), encoding='utf-8')
    seen = []
    original = content._validate_reporter_cards
    def observe(*a, **kw):
        seen.append(kw['category'])
        return original(*a, **kw)
    monkeypatch.setattr(content, '_validate_reporter_cards', observe)
    args['model_runner'] = lambda **_: pytest.fail('保存済み正常記事の再送')
    args['candidate_provider'] = lambda *_: pytest.fail('保存済み候補の再収集')
    result = content.produce_current_issue(**args)
    assert seen == ['fx', 'ai']
    assert result['model_call_count'] == 0
    assert result['dailyCardContractVersion'] == 1


def test_editor_cannot_change_the_reporter_score(tmp_path):
    from tests.test_news_grasp_daily_content import _summary
    payload = _payload()
    altered = {**payload['records'][0], 'score': 1}
    with pytest.raises(content.DailyContentError, match='reporter_binding'):
        content._validate_editor({'issue_date': payload['issue_date'],
            'summary_markdown': _summary(), 'append_records':[altered]},
            issue_date=payload['issue_date'], reporters=[payload], preview_dir=tmp_path)


def test_shortfall_requires_url_scoped_exclusion_reasons():
    payload = _payload()
    audit = {**payload['search_audit'], 'candidates':[
        {'url':payload['records'][0]['url']}, {'url':'https://example.com/other'}]}
    with pytest.raises(content.DailyContentError, match='shortfall_audit'):
        content._validate_reporter(payload, category='ai', issue_date=payload['issue_date'], search_audit=audit)
    payload['search_audit']['dropped'] = [{'url':'https://example.com/other','reason':'対象日の新しい事実を確認できない'}]
    assert content._validate_reporter(payload, category='ai', issue_date=payload['issue_date'], search_audit=audit)


def test_article_count_gate_invokes_shortfall_audit(tmp_path):
    from datetime import date
    from tools.validate_daily_quality import validate_digest_article_counts
    from tests.test_news_grasp_daily_content import _digest, ISSUE_DATE
    target = tmp_path / 'digest/AI' / f'{ISSUE_DATE}-AI.md'
    target.parent.mkdir(parents=True)
    target.write_text(_digest('ai'), encoding='utf-8')
    errors = validate_digest_article_counts(tmp_path / 'digest', date.fromisoformat(ISSUE_DATE))
    assert any('search audit missing' in error for error in errors)
