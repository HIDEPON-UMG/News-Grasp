"""日次の役割選択が実CLI引数まで届くことを検証する。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import model_policy, news_grasp_daily_content as content


@pytest.mark.parametrize('role,repair,expected', [
    ('reporter', None, ('gpt-5.6-luna', 'max')),
    ('reporter_shard', {'ai': {'reasonCode':'article_quality'}}, ('gpt-5.6-sol', 'max')),
    ('editor', None, ('gpt-5.6-sol', 'max')),
    ('daily_narration', None, ('gpt-5.6-luna', 'max')),
    ('deepdive', None, ('gpt-5.6-sol', 'high')),
    ('deepdive_review', None, ('gpt-5.6-sol', 'max')),
])
def test_role_specific_selection(role, repair, expected):
    chosen = model_policy.select_daily_model_config(role, repair_feedback=repair)
    assert (chosen['model'], chosen['reasoning']) == expected


def test_unknown_role_is_not_defaulted_to_luna():
    with pytest.raises(ValueError, match='DAILY_MODEL_ROLE_UNKNOWN'):
        model_policy.select_daily_model_config('misspelled_editor')


def test_runner_uses_selected_policy_in_actual_command(tmp_path, monkeypatch):
    from tools import model_spawn_client
    from tests.test_news_grasp_daily_output_schema import _copy_schemas, _repo_root
    _copy_schemas(_repo_root(), tmp_path / 'schemas')
    executable = tmp_path / 'codex.exe'
    executable.write_bytes(b'fixture')
    monkeypatch.setattr(content, '_model_prompt', lambda **_: 'テスト用の入力')
    monkeypatch.setitem(model_policy.DEFAULT_MODEL_POLICY['newsroom_editor'], 'default', 'gpt-5.6-sol')
    monkeypatch.setitem(model_policy.DEFAULT_MODEL_POLICY['newsroom_editor'], 'reasoning', 'high')
    seen = []
    def process(command, **kwargs):
        seen.append(command)
        Path(command[command.index('-o') + 1]).write_text(json.dumps({'fixture': True}), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
    monkeypatch.setattr(model_spawn_client, 'run_model_process', process)
    content._default_model_runner(role='editor', repo_root=tmp_path, issue_date='2026-09-07',
        run_id='model-selection-fixture', output_dir=tmp_path, codex_executable=executable)
    command, = seen
    assert command[command.index('-m') + 1] == 'gpt-5.6-sol'
    assert 'model_reasoning_effort="high"' in command
