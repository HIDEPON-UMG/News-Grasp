"""production adapter の distribution evidence 境界に対する追加回帰。"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tools import news_grasp_production_adapters as adapters


ISSUE_DATE = "2026-09-04"
RUN_ID = "daily-run-20260904-actual-001"
RUN_INTENT = "scheduled_production_direct"
MANIFEST_ID = "a" * 64
BUNDLE_ID = "daily-bundle-20260904-001"
RELEASE_SHA = "b" * 40


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _identity(tmp_path: Path, *, run_id: str = RUN_ID) -> dict[str, Any]:
    repo = tmp_path / "repo"
    repo.mkdir()
    context = {
        "repo_root": str(repo),
        "issue_date": ISSUE_DATE,
        "run_intent": RUN_INTENT,
        "run_id": run_id,
        "manifest_id": MANIFEST_ID,
        "bundle_id": BUNDLE_ID,
        "release_commit_sha": RELEASE_SHA,
    }
    return {
        "context": context,
        "repo_root": repo,
        "issue_date": ISSUE_DATE,
        "run_intent": RUN_INTENT,
        "run_id": run_id,
        "manifest_id": MANIFEST_ID,
        "bundle_id": BUNDLE_ID,
    }


def _seed_public_provider_state(
    identity: dict[str, Any],
    *,
    sent_count: int = 3,
    subscription_count: int = 3,
    notification_run_id: str | None = None,
) -> dict[str, Any]:
    root = Path(identity["repo_root"])
    day = str(identity["issue_date"])
    run_id = str(identity["run_id"])
    daily_path = root / "build" / "youtube-podcast" / "uploads.json"
    deep_path = root / "build" / "youtube-podcast-deepdive" / "uploads.json"
    _write_json(
        daily_path,
        {
            day: {
                "status": "public",
                "videoId": "daily-video-20260904",
                "playlistId": "daily-playlist",
                "playlistItemId": "daily-item-20260904",
            }
        },
    )
    _write_json(
        deep_path,
        {
            day: {
                "status": "public",
                "videoId": "deepdive-video-20260904",
                "playlistId": "deepdive-playlist",
                "playlistItemId": "deepdive-item-20260904",
                "primaryPodcastPlaylistId": "primary-podcast-playlist",
                "primaryPodcastPlaylistItemId": "primary-podcast-item-20260904",
            }
        },
    )
    _write_json(
        root / "build" / "tts" / "daily" / "latest_audio.json",
        {
            "issueDate": day,
            "runId": run_id,
            "status": "published",
            "url": f"https://example.invalid/audio/{day}.mp3",
        },
    )
    _write_json(
        root / "build" / "tts" / "deepdive" / "latest_audio.json",
        {
            "issueDate": day,
            "runId": run_id,
            "status": "published",
            "url": f"https://example.invalid/audio/deepdive-{day}.mp3",
        },
    )
    notification = {
        "schemaVersion": "NEWS_GRASP_NOTIFICATION_STATE_V2",
        "date": day,
        "run_id": notification_run_id or run_id,
        "run_intent": RUN_INTENT,
        "status": "sent",
        "subscription_count": subscription_count,
        "sent_count": sent_count,
        # providerが発行した固定receipt時刻をfixtureにも束縛し、再照合時の
        # distribution bytes再生成を検証する。
        "recorded_at": f"{day}T06:00:00+09:00",
        "deliveryReceiptV2": {
            "schemaVersion": "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V2",
            "issueDate": day,
            "runId": notification_run_id or run_id,
            "runIntent": RUN_INTENT,
            "status": "sent",
            "payloadIdentity": "1" * 64,
            "audienceIdentity": "2" * 64,
            "subscriptionCount": subscription_count,
            "sentCount": sent_count,
            "senderEventId": "fixture-provider-event",
        },
    }
    _write_json(root / "build" / "notification" / f"{day}.json", notification)
    return notification


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_distribution_evidence_accepts_snake_case_ledger_and_preserves_deepdive_primary_fields(
    tmp_path: Path,
) -> None:
    """snake_case 通知台帳から3種evidenceを同一bundle identityで生成する。"""

    identity = _identity(tmp_path)
    notification = _seed_public_provider_state(identity)

    result = adapters._materialize_distribution_evidence(identity, notification)
    root = Path(identity["repo_root"])
    distribution_path = root / result["distribution_path"]
    playlist_path = root / result["playlist_path"]
    binding_path = root / result["binding_path"]

    assert {distribution_path.name, playlist_path.name, binding_path.name} == {
        "2026-09-04.json",
        "playlist.json",
        "binding.json",
    }
    assert distribution_path.is_file()
    assert playlist_path.is_file()
    assert binding_path.is_file()

    distribution = _read_json(distribution_path)
    playlist = _read_json(playlist_path)
    binding = _read_json(binding_path)

    assert distribution["date"] == ISSUE_DATE
    assert distribution["run_id"] == RUN_ID
    assert distribution["run_intent"] == RUN_INTENT
    for evidence in (playlist, binding):
        assert evidence["issueDate"] == ISSUE_DATE
        assert evidence["runId"] == RUN_ID
        assert evidence["runIntent"] == RUN_INTENT
    assert distribution["manifest_id"] == MANIFEST_ID
    assert distribution["bundle_id"] == BUNDLE_ID
    assert distribution["pre_publish_commit"] == RELEASE_SHA
    assert distribution["publish_commit"] == RELEASE_SHA
    assert distribution["notification"]["sent_count"] == 3
    assert distribution["notification"]["subscription_count"] == 3

    assert playlist["status"] == "verified"
    assert playlist["deepdive"]["primaryPodcastPlaylistId"] == "primary-podcast-playlist"
    assert playlist["deepdive"]["primaryPodcastPlaylistItemId"] == "primary-podcast-item-20260904"
    assert distribution["playlist"]["deepdive"]["primaryPodcastPlaylistId"] == "primary-podcast-playlist"
    assert distribution["playlist"]["deepdive"]["primaryPodcastPlaylistItemId"] == "primary-podcast-item-20260904"
    assert playlist["receiptSha256"] == adapters._receipt_hash(playlist)
    assert binding["manifestId"] == MANIFEST_ID
    assert binding["bundleId"] == BUNDLE_ID
    assert binding["playlistReceiptSha256"] == playlist["receiptSha256"]
    assert binding["distributionSha256"] == hashlib.sha256(distribution_path.read_bytes()).hexdigest()
    assert result["distribution_sha256"] == binding["distributionSha256"]
    assert result["binding_receipt_sha256"] == binding["receiptSha256"]


def test_distribution_evidence_count_mismatch_creates_no_green_evidence(tmp_path: Path) -> None:
    """通知送信数と購読数の不一致は出力前にRedとなる。"""

    identity = _identity(tmp_path)
    notification = _seed_public_provider_state(
        identity,
        sent_count=2,
        subscription_count=3,
    )
    root = Path(identity["repo_root"])
    distribution_path = root / "data" / "distribution" / f"{ISSUE_DATE}.json"
    playlist_path = root / "build" / "distribution" / ISSUE_DATE / "playlist.json"
    binding_path = root / "build" / "distribution" / ISSUE_DATE / "binding.json"

    with pytest.raises(
        adapters.ProductionAdapterError,
        match="distribution_notification_delivery_count_invalid",
    ):
        adapters._materialize_distribution_evidence(identity, notification)

    assert not distribution_path.exists()
    assert not playlist_path.exists()
    assert not binding_path.exists()


def test_notification_provider_mock_rejects_run_identity_mismatch_before_distribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """providerが別runのledgerを書いた場合、Green証跡へ進めない。"""

    identity = _identity(tmp_path)
    repo = Path(identity["repo_root"])
    monkeypatch.setattr(adapters, "REPO_ROOT", repo)

    from tools import send_push

    def fake_main() -> int:
        state_path = Path(sys.argv[sys.argv.index("--record-state") + 1])
        _write_json(
            state_path,
            {
                "schemaVersion": "NEWS_GRASP_NOTIFICATION_STATE_V2",
                "date": ISSUE_DATE,
                "run_id": "different-actual-run",
                "run_intent": RUN_INTENT,
                "status": "sent",
                "subscription_count": 3,
                "sent_count": 3,
            },
        )
        return 0

    monkeypatch.setattr(send_push, "main", fake_main)
    monkeypatch.setattr(send_push, "default_body_for_today", lambda _weekday: "fixture body")

    with pytest.raises(
        adapters.ProductionAdapterError,
        match="notification_state_run_identity_mismatch",
    ):
        adapters._notification_send(
            context=identity["context"],
            operation_id="notification_send",
            side_effect_id="notification_send",
            idempotency_key="2026-09-04:notification_send:fixture",
            manifest_id=MANIFEST_ID,
            bundle_id=BUNDLE_ID,
            run_id=RUN_ID,
            fencing_token=1,
        )

    assert not (repo / "data" / "distribution" / f"{ISSUE_DATE}.json").exists()
    assert not (repo / "build" / "distribution" / ISSUE_DATE).exists()


def test_daily_audio_adapter_disables_history_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daily音声providerは既存履歴を回転させず、sealed runだけを更新する。"""

    identity = _identity(tmp_path)
    repo = Path(identity["repo_root"])
    monkeypatch.setattr(adapters, "REPO_ROOT", repo)
    audio_path = repo / "build" / "tts" / f"{ISSUE_DATE}.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"daily-audio-fixture")
    input_hash = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    identity["context"]["publish_seal"] = {
        "externalInputHashes": {
            f"build/tts/{ISSUE_DATE}.mp3": input_hash,
        },
    }

    calls: list[dict[str, Any]] = []
    from tools.tts import publish_audio

    def fake_publish(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, **kwargs})
        return {
            "latest_audio_date": ISSUE_DATE,
            "latest_audio_url": (
                "https://github.com/HIDEPON-UMG/News-Grasp/releases/download/"
                f"audio-daily/{ISSUE_DATE}-{input_hash[:12]}.mp3?v={input_hash[:12]}"
            ),
        }

    monkeypatch.setattr(publish_audio, "publish", fake_publish)
    result = adapters._audio_daily_upload(
        context=identity["context"],
        operation_id="audio_daily_upload",
        side_effect_id="audio_daily_upload",
        idempotency_key=f"{RUN_ID}:audio_daily_upload:fixture",
        manifest_id=MANIFEST_ID,
        bundle_id=BUNDLE_ID,
        run_id=RUN_ID,
        fencing_token=1,
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["dry_run"] is False
    assert calls[0]["run_id"] == RUN_ID
    assert calls[0]["rotate_history"] is False


def test_distribution_evidence_rejects_live_notification_binding_hash_drift_before_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider receipt後のlive state driftはdistribution Greenを生成しない。"""

    identity = _identity(tmp_path)
    repo = Path(identity["repo_root"])
    monkeypatch.setattr(adapters, "REPO_ROOT", repo)
    _seed_public_provider_state(identity)

    notification = _read_json(
        repo / "build" / "notification" / f"{ISSUE_DATE}.json"
    )
    state_path = repo / "build" / "notification" / f"{ISSUE_DATE}.json"
    # provider bindingが取得したsnapshotを保持したまま、materialize直前に
    # live notification stateだけが変わった境界を再現する。identity/countは
    # 同じなので、provider binding hashそのものを比較しなければ検出できない。
    live = dict(notification)
    live_receipt = dict(live["deliveryReceiptV2"])
    live_receipt["senderEventId"] = "drifted-provider-event"
    live["deliveryReceiptV2"] = live_receipt
    _write_json(state_path, live)

    with pytest.raises(
        adapters.ProductionAdapterError,
        match=(
            r"distribution_notification_(?:state_)?(?:hash|binding|identity)"
            r"|notification.*(?:hash|binding|identity)"
        ),
    ):
        adapters._materialize_distribution_evidence(identity, notification)

    assert not (repo / "data" / "distribution" / f"{ISSUE_DATE}.json").exists()
    assert not (repo / "build" / "distribution" / ISSUE_DATE / "playlist.json").exists()
    assert not (repo / "build" / "distribution" / ISSUE_DATE / "binding.json").exists()


def test_notification_recipient_ledger_resume_sends_only_unfinished_after_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N件中k件のterminal receipt後に停止しても、sentを再送せず残りだけ送る。"""

    from tools import send_push

    day = "2026-09-04"
    run_id = "notification-run-20260904"
    run_intent = "scheduled_production_direct"
    subscriptions = [
        {"endpoint": f"https://push.invalid/recipient-{index}"}
        for index in range(5)
    ]
    subscriptions_path = tmp_path / "subscriptions.json"
    subscriptions_path.write_text(
        json.dumps(subscriptions, ensure_ascii=False),
        encoding="utf-8",
    )
    key_path = tmp_path / "vapid.pem"
    key_path.write_text("fixture-key", encoding="utf-8")
    state_path = tmp_path / "notification.json"

    monkeypatch.setattr(send_push, "PUSH_WORKER_URL", "")
    monkeypatch.setattr(send_push, "PUBLISH_STATUS_FILE", tmp_path / "missing-status.json")
    monkeypatch.setattr(send_push, "_today_jst_str", lambda: day)
    monkeypatch.setattr(send_push, "_trusted_sender_source_binding", lambda _sha256: {})

    sent_endpoints: list[str] = []

    def fake_send_one(subscription, payload, vapid_key_file, claims_sub):
        del payload, vapid_key_file, claims_sub
        sent_endpoints.append(str(subscription["endpoint"]))
        return True, False, "fixture-provider-accepted"

    monkeypatch.setattr(send_push, "send_one", fake_send_one)

    class ProcessCrash(BaseException):
        pass

    original_append = send_push._append_recipient_event
    terminal_sent_count = 0
    crash_next_reservation = False
    crash_after = 2

    def append_event(path: Path, value: dict) -> None:
        nonlocal terminal_sent_count, crash_next_reservation
        # k件目のprovider成功とsent receiptが永続化された後、次のrecipient
        # reservation直前にprocess crashを注入する。次のrecipientのreserved
        # 行は残さないので、再開時にunknown_deliveryへ誤遷移しない。
        if crash_next_reservation and value.get("status") == "reserved":
            crash_next_reservation = False
            raise ProcessCrash("after_k_recipient_sent")
        original_append(path, value)
        if value.get("status") == "sent":
            terminal_sent_count += 1
            if terminal_sent_count == crash_after:
                crash_next_reservation = True

    monkeypatch.setattr(send_push, "_append_recipient_event", append_event)
    argv = [
        "send_push.py",
        "--subscriptions-file",
        str(subscriptions_path),
        "--token-file",
        str(tmp_path / "missing-token"),
        "--vapid-key-file",
        str(key_path),
        "--record-state",
        str(state_path),
        "--run-id",
        run_id,
        "--run-intent",
        run_intent,
        "--body",
        "fixture notification",
        "--url",
        "https://example.invalid/2026-09-04/",
        "--skip-prune",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(ProcessCrash, match="after_k_recipient_sent"):
        send_push.main()

    endpoints = [str(item["endpoint"]) for item in subscriptions]
    assert sent_endpoints == endpoints[:crash_after]
    assert not state_path.exists()

    assert send_push.main() == 0

    assert sent_endpoints == endpoints
    assert len(sent_endpoints) == len(set(sent_endpoints)) == len(subscriptions)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "sent"
    assert state["subscription_count"] == len(subscriptions)
    assert state["sent_count"] == len(subscriptions)
    event_path = state_path.with_name(f"{day}.recipient-events.jsonl")
    latest: dict[str, dict[str, Any]] = {}
    for line in event_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        latest[str(event["recipientKey"])] = event
    assert len(latest) == len(subscriptions)
    assert {str(event["status"]) for event in latest.values()} == {"sent"}
