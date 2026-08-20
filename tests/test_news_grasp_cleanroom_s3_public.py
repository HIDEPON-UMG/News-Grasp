"""S3 clean-room public plane のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s3_cases.json"
ISSUE_DATE = "2026-08-21"
TOKYO = ZoneInfo("Asia/Tokyo")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_fixture() -> dict[str, Any]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S3_CASES_V1"
    assert data["packetId"] == "NG-CLEANROOM-S3-RED-V1"
    assert tuple(data["requiredSurfaceIds"]) == ("web", "archive", "podcast")
    assert set(data["eligibleNotRequiredSurfaceIds"]).issubset(data["requiredSurfaceIds"])
    assert set(data["surfaceStatuses"]) == {"PENDING", "CONFIRMED", "NOT_REQUIRED", "FAILED", "UNKNOWN"}
    return data


def _observed_at(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 8, 21, 9, 0, tzinfo=TOKYO) + timedelta(seconds=offset_seconds)


def _runtime_root(tmp_path: Path, index: int) -> Path:
    root = tmp_path / f"日本語-公開面-{index}"
    (root / "control").mkdir(parents=True)
    return root


def _inventory(data: dict[str, Any], statuses: dict[str, str]) -> dict[str, Any]:
    surfaces = [
        {
            "surfaceId": surface_id,
            "status": statuses[surface_id],
            "artifactSha256": _canonical_sha256({"issueDate": ISSUE_DATE, "surfaceId": surface_id}),
        }
        for surface_id in data["requiredSurfaceIds"]
        if surface_id in statuses
    ]
    inventory: dict[str, Any] = {
        "schemaVersion": "PUBLIC_SURFACE_INVENTORY_V1",
        "issueDate": ISSUE_DATE,
        "requiredSurfaceIds": list(data["requiredSurfaceIds"]),
        "eligibleNotRequiredSurfaceIds": list(data["eligibleNotRequiredSurfaceIds"]),
        "surfaces": surfaces,
    }
    inventory["inventorySha256"] = _canonical_sha256(inventory)
    return inventory


class _Publisher:
    def __init__(self, error_type: type[Exception], *, lose_response_once: bool = False) -> None:
        self.error_type = error_type
        self.lose_response_once = lose_response_once
        self.calls: list[dict[str, Any]] = []
        self.queries: list[str] = []
        self.receipts: dict[str, dict[str, Any]] = {}
        self._lost = False

    def publish(self, request: dict[str, Any]) -> dict[str, Any]:
        request = deepcopy(request)
        key = request["idempotencyKey"]
        self.calls.append(request)
        receipt = self.receipts.setdefault(
            key,
            {
                "schemaVersion": "PUBLIC_SURFACE_RECEIPT_V1",
                "idempotencyKey": key,
                "surfaceId": request["surfaceId"],
                "status": "CONFIRMED",
                "terminalHash": _canonical_sha256(request),
            },
        )
        if self.lose_response_once and not self._lost:
            self._lost = True
            raise self.error_type("publisher response lost after durable receipt")
        return deepcopy(receipt)

    def query(self, idempotency_key: str) -> dict[str, Any] | None:
        self.queries.append(idempotency_key)
        receipt = self.receipts.get(idempotency_key)
        return deepcopy(receipt) if receipt is not None else None


class _Notifier:
    def __init__(self, error_type: type[Exception], *, lose_response_once: bool = False) -> None:
        self.error_type = error_type
        self.lose_response_once = lose_response_once
        self.calls: list[dict[str, Any]] = []
        self.queries: list[str] = []
        self.receipts: dict[str, dict[str, Any]] = {}
        self._lost = False

    def notify(self, request: dict[str, Any]) -> dict[str, Any]:
        request = deepcopy(request)
        key = request["idempotencyKey"]
        self.calls.append(request)
        receipt = self.receipts.setdefault(
            key,
            {
                "schemaVersion": "PUBLIC_NOTIFICATION_RECEIPT_V1",
                "idempotencyKey": key,
                "status": "CONFIRMED",
                "terminalHash": _canonical_sha256(request),
            },
        )
        if self.lose_response_once and not self._lost:
            self._lost = True
            raise self.error_type("notification response lost after durable receipt")
        return deepcopy(receipt)

    def query(self, idempotency_key: str) -> dict[str, Any] | None:
        self.queries.append(idempotency_key)
        receipt = self.receipts.get(idempotency_key)
        return deepcopy(receipt) if receipt is not None else None


def _controller(module: Any, root: Path, publisher: Any, notifier: Any) -> Any:
    return module.PublicController(
        root,
        publisher=publisher,
        notifier=notifier,
        boundary_hook=lambda _name: None,
    )


def _reconcile(
    controller: Any,
    inventory: dict[str, Any],
    *,
    scheduled_state: Any = "CONFIRMED",
    recovery_state: Any = "GREEN",
    readiness_state: Any = "GREEN",
) -> dict[str, Any]:
    return controller.reconcile(
        issue_date=ISSUE_DATE,
        scheduled_state=scheduled_state,
        recovery_state=recovery_state,
        readiness_state=readiness_state,
        inventory=inventory,
        observed_at=_observed_at(),
    )


def test_s3_surface_bitmap_property(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    for index, case in enumerate(data["bitmapCases"]):
        publisher = _Publisher(module.PublishResultUnknown)
        notifier = _Notifier(module.PublishResultUnknown)
        if any(status in {"FAILED", "UNKNOWN"} for status in case["statuses"].values()):
            with pytest.raises(module.PublicControlError) as caught:
                _reconcile(
                    _controller(module, _runtime_root(tmp_path, index), publisher, notifier),
                    _inventory(data, case["statuses"]),
                )
            assert getattr(caught.value, "reason", None) in {
                "PUBLIC_INCOMPLETE",
                "MANUAL_RECONCILIATION_REQUIRED",
            }
            assert not notifier.calls
            continue
        result = _reconcile(
            _controller(module, _runtime_root(tmp_path, index), publisher, notifier),
            _inventory(data, case["statuses"]),
        )
        eligible = all(
            status in {"CONFIRMED", "PENDING"}
            or status == "NOT_REQUIRED" and surface_id in data["eligibleNotRequiredSurfaceIds"]
            for surface_id, status in case["statuses"].items()
        )
        assert result["schemaVersion"] == "PUBLIC_RECONCILE_RESULT_V1"
        assert eligible
        assert result["publicState"] == "GREEN"


def test_s3_completed_surface_skip(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    for index, confirmed_surface in enumerate(data["replayCases"]):
        statuses = {surface_id: "PENDING" for surface_id in data["requiredSurfaceIds"]}
        statuses[confirmed_surface.removeprefix("confirmed_")] = "CONFIRMED"
        publisher = _Publisher(module.PublishResultUnknown, lose_response_once=index == 0)
        notifier = _Notifier(module.PublishResultUnknown)
        controller = _controller(module, _runtime_root(tmp_path, index), publisher, notifier)
        inventory = _inventory(data, statuses)
        try:
            _reconcile(controller, inventory)
        except (module.PublicControlError, module.PublishResultUnknown):
            pass
        _reconcile(controller, inventory)
        assert len({call["idempotencyKey"] for call in publisher.calls}) == len(publisher.calls)
        assert all(call["surfaceId"] != confirmed_surface.removeprefix("confirmed_") for call in publisher.calls)
        assert len(publisher.calls) == len(data["requiredSurfaceIds"]) - 1


def test_s3_partial_public_typed(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    for index, partial_case in enumerate(data["partialCases"]):
        statuses = {surface_id: "CONFIRMED" for surface_id in data["requiredSurfaceIds"]}
        if partial_case == "MISSING_REQUIRED":
            statuses.pop("podcast")
        else:
            statuses["podcast"] = partial_case
        publisher = _Publisher(module.PublishResultUnknown)
        notifier = _Notifier(module.PublishResultUnknown)
        with pytest.raises(module.PublicControlError) as caught:
            _reconcile(
                _controller(module, _runtime_root(tmp_path, index), publisher, notifier),
                _inventory(data, statuses),
            )
        assert getattr(caught.value, "reason", None) in {
            "PUBLIC_INCOMPLETE",
            "MANUAL_RECONCILIATION_REQUIRED",
        }
        assert not notifier.calls


def test_s3_public_never_overwrites_scheduled(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    root = _runtime_root(tmp_path, 0)
    scheduled_terminal = {"state": "FAILED", "terminalHash": "s" * 64, "resultBytes": "scheduled-failure"}
    scheduled_path = root / "control" / "scheduled-terminal.json"
    scheduled_path.write_text(json.dumps(scheduled_terminal, sort_keys=True), encoding="utf-8")
    before = scheduled_path.read_bytes()
    result = _reconcile(
        _controller(module, root, _Publisher(module.PublishResultUnknown), _Notifier(module.PublishResultUnknown)),
        _inventory(data, {surface_id: "CONFIRMED" for surface_id in data["requiredSurfaceIds"]}),
        scheduled_state=scheduled_terminal,
        recovery_state={"state": "GREEN", "terminalHash": "r" * 64},
        readiness_state={"state": "GREEN", "terminalHash": "d" * 64},
    )
    assert scheduled_path.read_bytes() == before
    assert result["lineages"]
    rows = {row["lineage"]: row for row in result["lineages"]}
    assert tuple(rows) == tuple(data["lineages"])
    assert len({row["terminalHash"] for row in rows.values()}) == 4
    assert rows["Scheduled"]["terminalHash"] == scheduled_terminal["terminalHash"]


def test_s3_notification_exactly_once(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    publisher = _Publisher(module.PublishResultUnknown)
    notifier = _Notifier(module.PublishResultUnknown, lose_response_once=True)
    controller = _controller(module, _runtime_root(tmp_path, 0), publisher, notifier)
    inventory = _inventory(data, {surface_id: "CONFIRMED" for surface_id in data["requiredSurfaceIds"]})
    try:
        _reconcile(controller, inventory)
    except (module.PublicControlError, module.PublishResultUnknown):
        pass
    _reconcile(controller, inventory)
    assert len(notifier.calls) == 1
    assert len({call["idempotencyKey"] for call in notifier.calls}) == 1
    assert notifier.queries
    assert notifier.queries[0] == notifier.calls[0]["idempotencyKey"]


def test_s3_report_scheduled_first() -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    projection = {
        "Scheduled": {"state": "FAILED", "terminalHash": "s" * 64},
        "Recovery": {"state": "GREEN", "terminalHash": "r" * 64},
        "Public": {"state": "GREEN", "terminalHash": "p" * 64},
        "Readiness": {"state": "RED", "terminalHash": "d" * 64},
    }
    report = module.render_scheduled_first_report(projection)
    rendered = json.dumps(report, ensure_ascii=False)
    positions = [rendered.index(lineage) for lineage in data["reportOrder"]]
    assert positions == sorted(positions)
    assert all(lineage in rendered for lineage in data["reportOrder"])
    assert report["overallState"] != "GREEN"


def _surface_integrity_receipt(row: Any, receipt_json: str | None, mutation: str) -> str:
    if receipt_json is None:
        receipt: dict[str, Any] = {
            "schemaVersion": "PUBLIC_SURFACE_RECEIPT_V1",
            "surfaceId": row["surface_id"],
            "idempotencyKey": row["idempotency_key"],
            "status": "CONFIRMED",
            "terminalHash": row["terminal_hash"] or ("a" * 64),
        }
    else:
        parsed = json.loads(receipt_json)
        assert isinstance(parsed, dict)
        receipt = parsed
    if mutation == "syntax":
        return "{not-json"
    if mutation == "schema":
        receipt.pop("schemaVersion")
    elif mutation == "status":
        receipt["status"] = "PENDING"
    elif mutation == "surfaceId":
        receipt["surfaceId"] = "other-surface"
    elif mutation == "artifactSha256":
        receipt["artifactSha256"] = "f" * 64
    elif mutation == "idempotencyKey":
        receipt["idempotencyKey"] = "tampered-idempotency"
    elif mutation == "terminalHash":
        receipt["terminalHash"] = "e" * 64
    return json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _notification_integrity_receipt(row: Any, mutation: str) -> str:
    receipt: dict[str, Any] = {
        "schemaVersion": "PUBLIC_NOTIFICATION_RECEIPT_V1",
        "idempotencyKey": row["idempotency_key"],
        "status": "CONFIRMED",
        "terminalHash": "a" * 64,
    }
    if mutation == "syntax":
        return "{not-json"
    if mutation == "schema":
        receipt.pop("schemaVersion")
    elif mutation == "status":
        receipt["status"] = "PENDING"
    elif mutation == "idempotencyKey":
        receipt["idempotencyKey"] = "tampered-notification"
    elif mutation == "terminalHash":
        receipt["terminalHash"] = "e" * 64
    return json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_s3_persisted_surface_rows_are_revalidated(tmp_path: Path) -> None:
    import sqlite3

    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    mutations = (
        "state",
        "attempt_disposition",
        "idempotency_key",
        "artifact_sha256",
        "terminal_hash",
        "receipt_syntax",
        "receipt_schema",
        "receipt_status",
        "receipt_surfaceId",
        "receipt_artifactSha256",
        "receipt_idempotencyKey",
        "receipt_terminalHash",
    )
    outcomes: list[dict[str, Any]] = []
    baseline_classes = (
        ("INVENTORY_CONFIRMED", "web", "CONFIRMED"),
        ("PUBLISHED_CONFIRMED", "web", "PENDING"),
        ("NOT_REQUIRED", "archive", "NOT_REQUIRED"),
    )
    for class_index, (baseline_class, surface_id, target_status) in enumerate(baseline_classes):
        for mutation_index, mutation in enumerate(mutations):
            index = class_index * 100 + mutation_index
            root = _runtime_root(tmp_path, index)
            statuses = {surface_id: "CONFIRMED" for surface_id in data["requiredSurfaceIds"]}
            statuses[surface_id] = target_status
            publisher = _Publisher(module.PublishResultUnknown)
            notifier = _Notifier(module.PublishResultUnknown)
            _reconcile(_controller(module, root, publisher, notifier), _inventory(data, statuses))
            with sqlite3.connect(root / "control" / "public-ledger-v1.sqlite3") as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT * FROM surfaces WHERE issue_date=? AND surface_id=?",
                    (data["issueDate"], surface_id),
                ).fetchone()
                assert row is not None
                if baseline_class == "PUBLISHED_CONFIRMED":
                    assert len(publisher.calls) == 1
                    assert row["receipt_json"] is not None
                    assert isinstance(json.loads(row["receipt_json"]), dict)
                else:
                    assert not publisher.calls
                    assert row["receipt_json"] is None
                if mutation == "state":
                    connection.execute("UPDATE surfaces SET state=? WHERE issue_date=? AND surface_id=?", ("PENDING", data["issueDate"], surface_id))
                elif mutation == "attempt_disposition":
                    connection.execute("UPDATE surfaces SET attempt_disposition=? WHERE issue_date=? AND surface_id=?", ("QUERY_REQUIRED", data["issueDate"], surface_id))
                elif mutation in {"idempotency_key", "artifact_sha256", "terminal_hash"}:
                    column, value = {
                        "idempotency_key": ("idempotency_key", "tampered-idempotency"),
                        "artifact_sha256": ("artifact_sha256", "f" * 64),
                        "terminal_hash": ("terminal_hash", "e" * 64),
                    }[mutation]
                    connection.execute(f"UPDATE surfaces SET {column}=? WHERE issue_date=? AND surface_id=?", (value, data["issueDate"], surface_id))
                else:
                    receipt_case = mutation.removeprefix("receipt_")
                    connection.execute("UPDATE surfaces SET receipt_json=? WHERE issue_date=? AND surface_id=?", (_surface_integrity_receipt(row, row["receipt_json"], receipt_case), data["issueDate"], surface_id))
                connection.commit()
            retry_publisher = _Publisher(module.PublishResultUnknown)
            retry_notifier = _Notifier(module.PublishResultUnknown)
            try:
                _reconcile(_controller(module, root, retry_publisher, retry_notifier), _inventory(data, statuses))
            except module.PublicControlError as caught:
                reason = caught.reason
            else:
                reason = "RETURNED"
            outcomes.append({"class": baseline_class, "mutation": mutation, "reason": reason, "publish": len(retry_publisher.calls), "query": len(retry_publisher.queries), "notify": len(retry_notifier.calls), "notifyQuery": len(retry_notifier.queries)})
    assert all(item["reason"] == "PUBLIC_LEDGER_CORRUPT" for item in outcomes), outcomes
    assert all(item["publish"] == 0 and item["query"] == 0 and item["notify"] == 0 and item["notifyQuery"] == 0 for item in outcomes)


def test_s3_persisted_notification_is_revalidated(tmp_path: Path) -> None:
    import sqlite3

    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    mutations = ("state", "attempt_disposition", "idempotency_key", "receipt_syntax", "receipt_schema", "receipt_status", "receipt_idempotencyKey", "receipt_terminalHash")
    outcomes: list[dict[str, Any]] = []
    for index, mutation in enumerate(mutations, start=300):
        root = _runtime_root(tmp_path, index)
        statuses = {surface_id: "CONFIRMED" for surface_id in data["requiredSurfaceIds"]}
        publisher = _Publisher(module.PublishResultUnknown)
        notifier = _Notifier(module.PublishResultUnknown)
        _reconcile(_controller(module, root, publisher, notifier), _inventory(data, statuses))
        with sqlite3.connect(root / "control" / "public-ledger-v1.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM notifications WHERE issue_date=?", (data["issueDate"],)).fetchone()
            assert row is not None
            if mutation == "state":
                connection.execute("UPDATE notifications SET state=? WHERE issue_date=?", ("PENDING", data["issueDate"]))
            elif mutation == "attempt_disposition":
                connection.execute("UPDATE notifications SET attempt_disposition=? WHERE issue_date=?", ("QUERY_REQUIRED", data["issueDate"]))
            elif mutation == "idempotency_key":
                connection.execute("UPDATE notifications SET idempotency_key=? WHERE issue_date=?", ("tampered-notification", data["issueDate"]))
            else:
                connection.execute("UPDATE notifications SET receipt_json=? WHERE issue_date=?", (_notification_integrity_receipt(row, mutation.removeprefix("receipt_")), data["issueDate"]))
            connection.commit()
        retry_publisher = _Publisher(module.PublishResultUnknown)
        retry_notifier = _Notifier(module.PublishResultUnknown)
        try:
            _reconcile(_controller(module, root, retry_publisher, retry_notifier), _inventory(data, statuses))
        except module.PublicControlError as caught:
            reason = caught.reason
        else:
            reason = "RETURNED"
        outcomes.append({"mutation": mutation, "reason": reason, "notify": len(retry_notifier.calls), "notifyQuery": len(retry_notifier.queries)})
    assert all(item["reason"] == "PUBLIC_LEDGER_CORRUPT" for item in outcomes), outcomes
    assert all(item["notify"] == 0 and item["notifyQuery"] == 0 for item in outcomes)


def test_s3_persisted_lineage_columns_are_revalidated(tmp_path: Path) -> None:
    import sqlite3

    module = importlib.import_module("tools.news_grasp_cleanroom_public")
    data = _load_fixture()
    outcomes: list[dict[str, Any]] = []
    for index, lineage in enumerate(("Scheduled", "Readiness"), start=400):
        root = _runtime_root(tmp_path, index)
        source = root / "source-immutable.json"
        source.write_bytes(b"scheduled-source-immutable-v1")
        before = source.read_bytes()
        statuses = {surface_id: "CONFIRMED" for surface_id in data["requiredSurfaceIds"]}
        scheduled = {"state": "FAILED", "terminalHash": "s" * 64}
        recovery = {"state": "GREEN", "terminalHash": "r" * 64}
        readiness = {"state": "RED", "terminalHash": "d" * 64}
        _reconcile(
            _controller(module, root, _Publisher(module.PublishResultUnknown), _Notifier(module.PublishResultUnknown)),
            _inventory(data, statuses),
            scheduled_state=scheduled,
            recovery_state=recovery,
            readiness_state=readiness,
        )
        with sqlite3.connect(root / "control" / "public-ledger-v1.sqlite3") as connection:
            connection.execute("UPDATE lineages SET state=? WHERE issue_date=? AND lineage=?", ("GREEN", data["issueDate"], lineage))
            connection.commit()
        retry_publisher = _Publisher(module.PublishResultUnknown)
        retry_notifier = _Notifier(module.PublishResultUnknown)
        try:
            result = _reconcile(
                _controller(module, root, retry_publisher, retry_notifier),
                _inventory(data, statuses),
                scheduled_state=scheduled,
                recovery_state=recovery,
                readiness_state=readiness,
            )
        except module.PublicControlError as caught:
            reason = caught.reason
            public_state = None
        else:
            reason = "RETURNED"
            public_state = result.get("publicState")
        outcomes.append({"lineage": lineage, "reason": reason, "publicState": public_state, "publish": len(retry_publisher.calls), "notify": len(retry_notifier.calls), "source": source.read_bytes()})
        assert source.read_bytes() == before
    assert all(item["reason"] == "PUBLIC_LEDGER_CORRUPT" for item in outcomes), outcomes
    assert all(item["publicState"] != "GREEN" for item in outcomes)
    assert all(item["publish"] == 0 and item["notify"] == 0 for item in outcomes)
