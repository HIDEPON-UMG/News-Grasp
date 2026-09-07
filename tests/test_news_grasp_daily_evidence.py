"""DeepDive reviewの入力束縛・有限予算・再利用境界。"""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest

AXES = ('theme_specific_insight', 'evidence_depth', 'causal_coherence',
        'counterevidence', 'decision_utility', 'dialogue_naturalness', 'relation_map_utility')


def _module():
    return importlib.import_module('tools.news_grasp_daily_evidence')


def _inputs():
    return {'issueDate':'2026-09-05', 'artifacts':{
        kind:{'path':f'digest/DeepDive/{kind}.md', 'sha256':hashlib.sha256(kind.encode()).hexdigest()}
        for kind in ('article','relation','dialogue')}, 'provenanceSha256':'a'*64}


def _raw(score=4):
    return {'scores':{axis:score for axis in AXES},
            'findings':{axis:'引用根拠から主体と制約の関係を検証した。' for axis in AXES}}


def test_review_cannot_supply_success_or_artifact_identity():
    with pytest.raises(ValueError, match='review_output_shape'):
        _module().bind_review_output(_inputs(), {**_raw(), 'status':'Green'})


def test_low_score_remains_red_and_keeps_findings():
    result = _module().bind_review_output(_inputs(), _raw(3))
    assert result['status'] == 'Red'
    assert result['averageScore'] == 3
    assert result['findings'] == _raw(3)['findings']


@pytest.mark.parametrize('score', [True, 0, 6, 4.0, '4'])
def test_invalid_score_never_becomes_review(score):
    raw = _raw()
    raw['scores']['evidence_depth'] = score
    with pytest.raises(ValueError, match='review_score_invalid'):
        _module().bind_review_output(_inputs(), raw)


def test_review_binding_rejects_changed_raw_or_inputs():
    m = _module()
    inputs, raw = _inputs(), _raw()
    review = m.bind_review_output(inputs, raw)
    raw_bytes = json.dumps(raw).encode()
    binding = m.review_binding(inputs, review, call_id='quality-call', raw_bytes=raw_bytes)
    assert m.validate_review_binding(inputs, review, binding, raw_bytes=raw_bytes)
    with pytest.raises(ValueError, match='review_binding_invalid'):
        m.validate_review_binding(inputs, review, binding, raw_bytes=b'{}')
    with pytest.raises(ValueError, match='review_binding_invalid'):
        m.validate_review_binding({**inputs,'provenanceSha256':'b'*64}, review, binding, raw_bytes=raw_bytes)


def test_evidence_precedes_all_deepdive_media():
    from tools.news_grasp_repair_registry import build_daily_artifact_dag
    dag = build_daily_artifact_dag(['ai'])
    assert dag['deepdive_evidence']['dependsOn'] == ['deepdive_article','deepdive_dialogue']
    for artifact in ('deepdive_html','deepdive_audio','deepdive_video'):
        assert 'deepdive_evidence' in dag[artifact]['dependsOn']


def test_review_repair_targets_the_causal_content_fields():
    m = _module()
    raw = _raw()
    raw['scores']['dialogue_naturalness'] = 2
    assert m.review_repair_fields(m.bind_review_output(_inputs(), raw)) == ['dialogue_markdown']
    raw['scores']['evidence_depth'] = 2
    assert set(m.review_repair_fields(m.bind_review_output(_inputs(), raw))) == {'article_markdown','dialogue_markdown'}
