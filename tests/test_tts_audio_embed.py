from __future__ import annotations

from tools.generate_pages import _get_jinja_env


def _render(template_name: str, *, latest_audio_url: str = "", latest_audio_date: str = "") -> str:
    template = _get_jinja_env().get_template(template_name)
    return template.render(
        site_title="News Grasp",
        site_tagline="Seven lenses",
        base_url="https://hidepon-umg.github.io/News-Grasp",
        canonical="https://hidepon-umg.github.io/News-Grasp/2026-06-16/summary/",
        today_date="2026-06-16",
        today_weekday="火",
        date="2026-06-16",
        issue_no="20260616",
        categories=[],
        editor_top3=[],
        lens_cards=[],
        stats={"sections": 9, "read_min": 9, "takeaways": 3, "sources": 35},
        sections=[],
        takeaways=[],
        latest_audio_url=latest_audio_url,
        latest_audio_date=latest_audio_date,
    )


def test_index_template_embeds_latest_release_audio_when_url_is_present():
    url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3"

    html = _render("index-template.html", latest_audio_url=url, latest_audio_date="2026-06-16")

    assert '<audio preload="none" controls' in html
    assert f'src="{url}"' in html
    assert "2026-06-16" in html
    for rate in ("1", "1.25", "1.5", "2"):
        assert f'data-rate="{rate}"' in html
    assert "playbackRate" in html


def test_summary_template_embeds_latest_release_audio_when_url_is_present():
    url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3"

    html = _render("summary-template.html", latest_audio_url=url, latest_audio_date="2026-06-16")

    assert '<audio preload="none" controls' in html
    assert f'src="{url}"' in html
    assert "2026-06-16" in html
    for rate in ("1", "1.25", "1.5", "2"):
        assert f'data-rate="{rate}"' in html
    assert "playbackRate" in html


def test_audio_block_is_absent_without_latest_audio_url():
    assert '<audio preload="none" controls' not in _render("index-template.html")
    assert '<audio preload="none" controls' not in _render("summary-template.html")
