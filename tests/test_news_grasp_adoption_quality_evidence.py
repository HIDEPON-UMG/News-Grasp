"""公開への採用時にも、本文へ束縛された品質証拠を保持する。"""
import hashlib
import json
from pathlib import Path

import pytest

from tools import deepdive_quality as quality
from tools import news_grasp_artifact_adoption as adoption


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    issue = '2026-09-05'
    payload = {'article_markdown': '本文\n', 'dialogue_markdown': '対話\n'}
    names = {
        'article': f'digest/DeepDive/{issue}-DeepDive.md',
        'dialogue': f'digest/DeepDive/{issue}-DeepDive-dialogue.md',
        'provenance': f'data/deepdive-provenance/{issue}.json',
        'quality_review': f'data/deepdive-quality-review/{issue}.json',
    }
    values = {'article': payload['article_markdown'].encode(), 'dialogue': payload['dialogue_markdown'].encode(), 'provenance': b'{}'}
    values['quality_review'] = json.dumps({'issueDate': issue, 'artifacts': {'dialogue': {'path': names['dialogue'], 'sha256': hashlib.sha256(values['dialogue']).hexdigest()}}}).encode()
    for key, raw in values.items():
        path = tmp_path / names[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    audit = {'status': 'Green', 'issues': [], 'issueCodes': [], 'auditedFiles': [
        {'path': str((tmp_path / names[key]).resolve()), 'sha256': hashlib.sha256(values[key]).hexdigest()}
        for key in ('article', 'provenance', 'quality_review')]}
    monkeypatch.setattr(quality, 'audit_issue', lambda **kwargs: audit)
    return tmp_path, issue, payload, names, values, audit


def test_saved_evidence_is_carried_without_other_paths(evidence):
    root, issue, payload, names, values, _ = evidence
    result = adoption._capture_quality_evidence(root, issue, payload)
    assert result == {names[key]: values[key] for key in ('provenance', 'quality_review')}


@pytest.mark.parametrize('kind', ['article', 'dialogue', 'provenance', 'quality_review', 'audit_red', 'audit_missing', 'payload'])
def test_adoption_rejects_stale_or_unbound_evidence(evidence, kind):
    root, issue, payload, names, _, audit = evidence
    if kind in names:
        (root / names[kind]).write_bytes(b'changed')
    elif kind == 'audit_red':
        audit['status'] = 'Red'
    elif kind == 'audit_missing':
        audit['auditedFiles'].pop()
    else:
        payload['article_markdown'] = '別の本文\n'
    with pytest.raises(ValueError):
        adoption._capture_quality_evidence(root, issue, payload)
