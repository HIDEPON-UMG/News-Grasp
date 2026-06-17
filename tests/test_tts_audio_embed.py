from __future__ import annotations

import hashlib
import json

from tools.generate_pages import _get_jinja_env


def _render(
    template_name: str,
    *,
    latest_audio_url: str = "",
    latest_audio_date: str = "",
    is_yesterday: bool = False,
    audio_label: str = "今日のニュース朗読",
) -> str:
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
        is_yesterday=is_yesterday,
        audio_label=audio_label,
    )


def test_index_template_embeds_latest_release_audio_when_url_is_present():
    url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3"

    html = _render("index-template.html", latest_audio_url=url, latest_audio_date="2026-06-16")

    assert '<audio preload="none" controls' in html
    assert f'<source src="{url}" type="audio/mpeg">' in html
    assert '<audio preload="none" controls src=' not in html
    assert "2026-06-16" in html
    for rate in ("1", "1.25", "1.5", "2"):
        assert f'data-rate="{rate}"' in html
    assert "playbackRate" in html


def test_summary_template_embeds_latest_release_audio_when_url_is_present():
    url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3"

    html = _render("summary-template.html", latest_audio_url=url, latest_audio_date="2026-06-16")

    assert '<audio preload="none" controls' in html
    assert f'<source src="{url}" type="audio/mpeg">' in html
    assert '<audio preload="none" controls src=' not in html
    assert "2026-06-16" in html
    for rate in ("1", "1.25", "1.5", "2"):
        assert f'data-rate="{rate}"' in html
    assert "playbackRate" in html


def test_audio_block_is_absent_without_latest_audio_url():
    assert '<audio preload="none" controls' not in _render("index-template.html")
    assert '<audio preload="none" controls' not in _render("summary-template.html")


def test_yesterday_index_template_labels_audio_as_yesterday():
    url = "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-15.mp3"

    html = _render(
        "index-template.html",
        latest_audio_url=url,
        latest_audio_date="2026-06-15",
        is_yesterday=True,
        audio_label="昨日のニュース朗読",
    )

    assert 'aria-label="昨日のニュース朗読"' in html
    assert "昨日のニュース朗読" in html
    assert "今日のニュース朗読" not in html


def test_latest_audio_for_pages_falls_back_to_requested_local_mp3(tmp_path, monkeypatch):
    from tools import generate_pages

    tts_dir = tmp_path / "tts"
    tts_dir.mkdir()
    previous_mp3 = tts_dir / "2026-06-15.mp3"
    previous_mp3.write_bytes(b"previous-day-audio")
    latest_json = tts_dir / "latest_audio.json"
    latest_json.write_text(
        json.dumps(
            {
                "latest_audio_date": "2026-06-16",
                "latest_audio_url": "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/2026-06-16.mp3?v=current",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(generate_pages, "_LATEST_AUDIO_JSON", latest_json)
    monkeypatch.setattr(generate_pages, "_TTS_BUILD_DIR", tts_dir)

    got = generate_pages.latest_audio_for_pages("2026-06-15")

    digest = hashlib.sha256(b"previous-day-audio").hexdigest()[:12]
    assert got == {
        "latest_audio_date": "2026-06-15",
        "latest_audio_url": (
            "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
            f"audio-daily/2026-06-15.mp3?v={digest}"
        ),
    }
