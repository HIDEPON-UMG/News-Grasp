"""YouTube Podcast playlist membership repair の sealed Red suite。

この suite は YouTube transport だけを fake し、監査・権限・削除順序は
本番モジュールの実装を通して観測する。baseline には repair route がないため、
import は成功したまま各 Red node が欠落した振る舞いを示す。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest


MODULE = importlib.import_module("tools.youtube_podcast.upload_episode")
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "youtube_podcast_playlist_repair_cases.json"
AUTHORITY_SCHEMA = "NEWS_GRASP_PLAYLIST_MEMBERSHIP_REPAIR_AUTHORITY_V1"
RESULT_SCHEMA = "NEWS_GRASP_PLAYLIST_MEMBERSHIP_REPAIR_RESULT_V1"
ISSUE_DATE = "2026-08-20"


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utc_rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class FakeYouTubeTransport:
    """YouTube API 境界だけを fake する transport。

    ``delete_noop`` は post-audit residual を再現し、動画削除 surface は明示的に
    fail させることで、playlist membership と video object の境界を検証する。
    """

    def __init__(self, items: list[dict[str, Any]], *, delete_noop: bool = False) -> None:
        self._items = {"playlist-daily-2026": deepcopy(items)}
        self.delete_noop = delete_noop
        self.calls: list[tuple[str, str]] = []

    @property
    def deleted_playlist_item_ids(self) -> list[str]:
        return [value for name, value in self.calls if name == "playlistItems.delete"]

    @property
    def video_deletion_calls(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0].startswith("videos.")]

    def list_playlist_items(self, *, playlist_id: str) -> list[dict[str, Any]]:
        self.calls.append(("playlistItems.list", playlist_id))
        return deepcopy(self._items.get(playlist_id, []))

    def delete_playlist_item(self, playlist_item_id: str) -> dict[str, Any]:
        self.calls.append(("playlistItems.delete", playlist_item_id))
        if not self.delete_noop:
            for playlist_id, items in self._items.items():
                self._items[playlist_id] = [
                    item for item in items if str(item.get("playlistItemId")) != playlist_item_id
                ]
        return {"id": playlist_item_id}

    def delete_video(self, video_id: str) -> None:
        self.calls.append(("videos.delete", video_id))
        raise AssertionError("playlist repair must never delete a video object")

    def __getattr__(self, name: str) -> Any:
        if name in {
            "ensure_playlist",
            "upload_video",
            "update_video_privacy",
            "add_video_to_playlist",
        }:
            raise AssertionError(f"playlist repair called unrelated YouTube surface: {name}")
        raise AttributeError(name)


@pytest.fixture
def repair_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    build_dir = tmp_path / "build" / "youtube-podcast"
    build_dir.mkdir(parents=True)
    uploads_path = build_dir / "uploads.json"
    uploads = {
        ISSUE_DATE: {
            "status": "public",
            "videoId": fixture["videoId"],
            "playlistId": fixture["playlistId"],
            "playlistItemId": "item-keep",
            "mp4_sha256": "a" * 64,
        }
    }
    uploads_path.write_text(json.dumps(uploads, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(MODULE, "BUILD_DIR", build_dir)
    monkeypatch.setattr(MODULE, "DEEPDIVE_BUILD_DIR", tmp_path / "build" / "youtube-podcast-deepdive")
    return {
        "fixture": fixture,
        "uploads_path": uploads_path,
        "items": fixture["items"],
    }


def _audit(client: FakeYouTubeTransport) -> dict[str, Any]:
    return MODULE.audit_playlist_uniqueness(ISSUE_DATE, client=client)


def _make_authority(client: FakeYouTubeTransport) -> dict[str, Any]:
    audit = _audit(client)
    unexpected_ids = sorted(
        {
            str(issue["playlistItemId"])
            for issue in audit["issues"]
            if issue.get("reason") == "podcast_playlist_unexpected_same_date_video"
            and issue.get("playlistItemId")
        }
    )
    now = datetime.now(timezone.utc)
    authority = {
        "schemaVersion": AUTHORITY_SCHEMA,
        "issueDate": ISSUE_DATE,
        "action": "delete_playlist_memberships",
        "playlistItemIds": unexpected_ids,
        "preserveVideoObjects": True,
        "issuedAt": _utc_rfc3339(now - timedelta(minutes=1)),
        "expiresAt": _utc_rfc3339(now + timedelta(hours=1)),
        "auditSha256": _canonical_sha256(audit),
    }
    authority["receiptSha256"] = _canonical_sha256(authority)
    return authority


def _repair_entrypoint() -> Any:
    entrypoint = getattr(MODULE, "repair_playlist_memberships", None)
    assert callable(entrypoint), "repair_playlist_memberships route is missing"
    return entrypoint


def _call_repair(
    client: FakeYouTubeTransport,
    authority: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    result = _repair_entrypoint()(
        ISSUE_DATE,
        authority,
        client=client,
        dry_run=dry_run,
    )
    assert isinstance(result, dict), "repair result must be JSON-serializable object"
    return result


def _assert_rejected(
    client: FakeYouTubeTransport,
    authority: dict[str, Any],
    *,
    uploads_path: Path | None = None,
    expected_deleted_playlist_item_ids: list[str] | None = None,
) -> None:
    before = uploads_path.read_bytes() if uploads_path else None
    entrypoint = _repair_entrypoint()
    try:
        result = entrypoint(ISSUE_DATE, authority, client=client, dry_run=False)
    except Exception:
        result = None
    else:
        assert result.get("status") != "ok", "invalid authority unexpectedly succeeded"
        assert result.get("ok") is not True, "invalid authority unexpectedly reported Green"
    expected_deleted = expected_deleted_playlist_item_ids or []
    assert client.deleted_playlist_item_ids == expected_deleted
    assert client.video_deletion_calls == []
    if uploads_path:
        assert uploads_path.read_bytes() == before


def test_authorized_repair_deletes_only_audited_playlist_item_once_and_preserves_video_objects(
    repair_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeYouTubeTransport(repair_context["items"])
    authority = _make_authority(client)
    client.calls.clear()
    original_audit = MODULE.audit_playlist_uniqueness
    observed_audits: list[dict[str, Any]] = []

    def audit_spy(day: str, *, client: FakeYouTubeTransport | None = None) -> dict[str, Any]:
        result = original_audit(day, client=client)
        observed_audits.append(deepcopy(result))
        return result

    monkeypatch.setattr(MODULE, "audit_playlist_uniqueness", audit_spy)
    result = _call_repair(client, authority)

    assert result["schemaVersion"] == RESULT_SCHEMA
    assert result["status"] == "ok"
    assert result["issueDate"] == ISSUE_DATE
    assert result["dryRun"] is False
    assert result["deletedPlaylistItemIds"] == ["item-unexpected"]
    assert client.deleted_playlist_item_ids == ["item-unexpected"]
    assert client.video_deletion_calls == []
    assert len(observed_audits) >= 2, "repair must reuse audit_playlist_uniqueness before and after mutation"
    assert observed_audits[0]["issues"]
    assert observed_audits[-1]["ok"] is True
    assert result["auditBefore"] == observed_audits[0]
    assert result["auditAfter"] == observed_audits[-1]


def test_dry_run_performs_zero_youtube_mutation_and_returns_typed_result(
    repair_context: dict[str, Any],
) -> None:
    client = FakeYouTubeTransport(repair_context["items"])
    authority = _make_authority(client)
    client.calls.clear()
    before_items = deepcopy(client._items)
    before_uploads = repair_context["uploads_path"].read_bytes()

    result = _call_repair(client, authority, dry_run=True)

    assert result["schemaVersion"] == RESULT_SCHEMA
    assert result["status"] == "ok"
    assert result["dryRun"] is True
    assert result["deletedPlaylistItemIds"] == []
    assert client.deleted_playlist_item_ids == []
    assert client.video_deletion_calls == []
    assert client._items == before_items
    assert repair_context["uploads_path"].read_bytes() == before_uploads


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("missing_issue_date", lambda authority: authority.pop("issueDate")),
        (
            "stale_expiry",
            lambda authority: authority.update({"expiresAt": "2020-01-01T00:00:00Z"}),
        ),
        (
            "cross_date",
            lambda authority: authority.update({"issueDate": "2026-08-19"}),
        ),
        (
            "cross_date_item",
            lambda authority: authority.update({"playlistItemIds": ["item-other-date"]}),
        ),
        (
            "unexpected_set_drift",
            lambda authority: authority.update({"playlistItemIds": []}),
        ),
        (
            "duplicate_ids",
            lambda authority: authority.update({"playlistItemIds": ["item-unexpected", "item-unexpected"]}),
        ),
        (
            "action_drift",
            lambda authority: authority.update({"action": "delete_videos"}),
        ),
        (
            "preserve_video_objects_drift",
            lambda authority: authority.update({"preserveVideoObjects": False}),
        ),
        (
            "audit_hash_drift",
            lambda authority: authority.update({"auditSha256": "0" * 64}),
        ),
        (
            "receipt_hash_drift",
            lambda authority: authority.update({"receiptSha256": "0" * 64}),
        ),
    ],
)
def test_authority_missing_or_drifted_fields_fail_closed_before_mutation(
    repair_context: dict[str, Any],
    case: str,
    mutate: Any,
) -> None:
    client = FakeYouTubeTransport(repair_context["items"])
    authority = _make_authority(client)
    mutate(authority)
    if case not in {"missing_issue_date", "receipt_hash_drift", "audit_hash_drift"}:
        authority.pop("receiptSha256", None)
        authority["receiptSha256"] = _canonical_sha256(authority)
    elif case == "audit_hash_drift":
        authority.pop("receiptSha256", None)
        authority["receiptSha256"] = _canonical_sha256(authority)

    _assert_rejected(client, authority, uploads_path=repair_context["uploads_path"])


def test_post_audit_residual_fails_closed_after_exactly_once_attempt(
    repair_context: dict[str, Any],
) -> None:
    client = FakeYouTubeTransport(repair_context["items"], delete_noop=True)
    authority = _make_authority(client)
    client.calls.clear()

    _assert_rejected(
        client,
        authority,
        expected_deleted_playlist_item_ids=["item-unexpected"],
    )

    assert client.deleted_playlist_item_ids == ["item-unexpected"]
    assert client.video_deletion_calls == []


def _run_cli(argv: list[str]) -> tuple[int, list[str]]:
    try:
        code = MODULE.main(argv)
    except SystemExit as exc:
        code = int(exc.code or 0)
    return code, []


def test_cli_exposes_authorized_repair_and_keeps_audit_playlists_read_only(
    repair_context: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeYouTubeTransport(repair_context["items"])
    authority = _make_authority(client)
    authority_path = tmp_path / "playlist-repair-authority.json"
    authority_path.write_text(json.dumps(authority, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        MODULE.YouTubePodcastClient,
        "from_local_secrets",
        staticmethod(lambda *_args, **_kwargs: client),
    )

    audit_code, _ = _run_cli([ISSUE_DATE, "--audit-playlists"])
    assert audit_code == 1, "audit must remain read-only but report the seeded unexpected item"
    assert client.deleted_playlist_item_ids == []
    assert client.video_deletion_calls == []
    capsys.readouterr()

    # The audit above did not mutate transport; the exact fixture remains authorized.
    client.calls.clear()
    repair_code, _ = _run_cli(
        [
            ISSUE_DATE,
            "--repair-playlists",
            "--authority-file",
            str(authority_path),
        ]
    )
    assert repair_code == 0
    assert client.deleted_playlist_item_ids == ["item-unexpected"]
    assert client.video_deletion_calls == []
    output_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["schemaVersion"] == RESULT_SCHEMA
    assert payload["status"] == "ok"
