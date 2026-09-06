"""コード修復後も保存runを公開でき、公開データ競合は拒否する。"""
import pytest

from tools import news_grasp_daily_release as release


@pytest.mark.parametrize('path,accepted', [('tools/generate_pages.py',True),('tests/test_page.py',True),('schemas/page.json',True),('data/articles.jsonl',False),('docs/index.html',False)])
def test_only_source_changes_can_advance_saved_publish_parent(tmp_path,monkeypatch,path,accepted):
    monkeypatch.setattr(release,'_git',lambda root,args,**kwargs: path+'\0' if 'diff' in args else f'100644 blob {"c"*40}\t{path}\0' if 'ls-tree' in args else '')
    kwargs=dict(source_baseline='a'*40,remote_base_sha='a'*40,candidate_base='b'*40)
    if accepted:
        result=release.validate_release_base_transition(tmp_path,**kwargs)
        assert isinstance(result,dict)
    else:
        with pytest.raises(release.DailyReleaseError):
            release.validate_release_base_transition(tmp_path,**kwargs)


def test_nonancestor_is_not_a_recoverable_source_update(tmp_path,monkeypatch):
    def git(root,args,**kwargs):
        if 'merge-base' in args:
            raise release.DailyReleaseError('nonancestor')
        return 'tools/example.py\0'
    monkeypatch.setattr(release,'_git',git)
    with pytest.raises(release.DailyReleaseError):
        release.validate_release_base_transition(tmp_path,source_baseline='a'*40,remote_base_sha='a'*40,candidate_base='b'*40)


def test_invalid_identity_rejected_without_git(tmp_path,monkeypatch):
    monkeypatch.setattr(release,'_git',lambda *args,**kwargs: pytest.fail('無効な入力でgitを呼ばない'))
    with pytest.raises(release.DailyReleaseError):
        release.validate_release_base_transition(tmp_path,source_baseline='invalid',remote_base_sha='a'*40,candidate_base='b'*40)
