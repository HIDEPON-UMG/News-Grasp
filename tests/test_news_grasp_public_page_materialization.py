"""保存digestから必要な公開面を生成し、本文と別日個別面を保持する。"""
from pathlib import Path

import pytest

from tools import generate_pages as pages
from tools import news_grasp_daily_content as content


@pytest.mark.parametrize('folder,filename,expected', [('AI','2026-09-05-AI.md','2026-09-05'), ('DeepDive','2026-09-05-DeepDive.md','')])
def test_saved_digest_date_without_frontmatter(tmp_path,folder,filename,expected):
    path=tmp_path/folder/filename
    path.parent.mkdir()
    raw='### [1] 保存記事\n\n- [記事を読む](https://example.com/article)\n'
    path.write_text(raw,encoding='utf-8')
    assert pages.build_context(path)['date']==expected
    assert path.read_text(encoding='utf-8')==raw


def test_site_projection_includes_current_issue_and_indexes_only(tmp_path,monkeypatch):
    from tools import render_deepdive
    issue='2026-09-05'
    calls=[]
    monkeypatch.setattr(pages,'scan_digests',lambda: [])
    monkeypatch.setattr(pages,'_collect_entries',lambda x: [{'date':issue}])
    monkeypatch.setattr(render_deepdive,'collect_archive_items',lambda: {'items':[],'chips':[]})
    def builder(name,many=False):
        def run(*args,**kwargs):
            calls.append((name,args,kwargs))
            path=tmp_path/'docs'/name/'index.html'
            return [path] if many else path
        return run
    for name in ('build_all','build_index','build_category_pages','build_overview','build_summary','build_archive'):
        monkeypatch.setattr(pages,name,builder(name,name in {'build_all','build_category_pages'}))
    actions={name:'reuse' for name in ('daily_audio_script','daily_audio','daily_audio_projection','daily_video','deepdive_html','deepdive_audio','deepdive_audio_projection','deepdive_video')}
    actions['site_html']='rebuild_deterministic'
    result=content._default_derived_builder(repo_root=tmp_path,issue_date=issue,run_id='fixture',repair_actions=actions)
    assert {name for name,_,_ in calls}=={'build_all','build_index','build_category_pages','build_overview','build_summary','build_archive'}
    assert all(args[0]==issue for name,args,_ in calls if name in {'build_overview','build_summary'})
    assert len(result['artifacts'])==6
