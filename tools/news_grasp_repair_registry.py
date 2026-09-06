"""News-Grasp Daily artifact DAGと最小RepairPlanの正本。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


REPAIR_PLAN_SCHEMA = "NEWS_GRASP_REPAIR_PLAN_V1"
PRODUCER_ACTION = {
    "model": "repair_model",
    "deterministic": "rebuild_deterministic",
    "external": "reconcile_external",
}
MAX_INITIAL_MODEL_CALLS = 5
MAX_REPAIR_MODEL_CALLS = 4


class NewsGraspRepairPlanError(RuntimeError):
    """DAG、failure、RepairPlanのintegrity違反。"""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _node(
    *,
    depends_on: Sequence[str] = (),
    producer_kind: str,
    owner: str,
) -> dict[str, Any]:
    return {
        "dependsOn": list(depends_on),
        "producerKind": producer_kind,
        "owner": owner,
    }


def build_daily_artifact_dag(categories: Sequence[str]) -> dict[str, dict[str, Any]]:
    """当日カテゴリからDailyの全artifact依存を決定論的に構成する。"""

    normalized = tuple(str(item).strip() for item in categories)
    if not normalized or any(not item or "|" in item or ":" in item for item in normalized):
        raise NewsGraspRepairPlanError("NG_ARTIFACT_DAG_CATEGORY_INVALID")
    if len(set(normalized)) != len(normalized):
        raise NewsGraspRepairPlanError("NG_ARTIFACT_DAG_CATEGORY_DUPLICATE")

    dag: dict[str, dict[str, Any]] = {}
    for category in normalized:
        dag[f"candidate:{category}"] = _node(
            producer_kind="deterministic",
            owner="candidate_provider",
        )
    for category in normalized:
        reporter = f"reporter:{category}"
        dag[reporter] = _node(
            depends_on=(f"candidate:{category}",),
            producer_kind="model",
            owner="reporter",
        )
        for prefix, owner in (
            ("reporter_records", "reporter_materializer"),
            ("search_audit", "reporter_materializer"),
            ("digest", "reporter_materializer"),
        ):
            dag[f"{prefix}:{category}"] = _node(
                depends_on=(reporter,),
                producer_kind="deterministic",
                owner=owner,
            )

    reporter_ids = tuple(f"reporter:{category}" for category in normalized)
    dag["editor"] = _node(
        depends_on=reporter_ids,
        producer_kind="model",
        owner="newsroom_editor",
    )
    dag["articles_jsonl"] = _node(
        depends_on=("editor",),
        producer_kind="deterministic",
        owner="articles_materializer",
    )
    dag["summary"] = _node(
        depends_on=("editor",),
        producer_kind="deterministic",
        owner="summary_materializer",
    )
    dag["daily_audio_script"] = _node(
        depends_on=("summary", "editor"),
        producer_kind="deterministic",
        owner="daily_audio_builder",
    )
    dag["daily_audio"] = _node(
        depends_on=("daily_audio_script",),
        producer_kind="deterministic",
        owner="daily_audio_builder",
    )
    dag["daily_audio_projection"] = _node(
        depends_on=("daily_audio",),
        producer_kind="deterministic",
        owner="daily_audio_builder",
    )
    dag["daily_video"] = _node(
        depends_on=("daily_audio", "summary"),
        producer_kind="deterministic",
        owner="daily_video_builder",
    )
    dag["deepdive_model"] = _node(
        depends_on=("editor",),
        producer_kind="model",
        owner="deepdive",
    )
    dag["deepdive_article"] = _node(
        depends_on=("deepdive_model",),
        producer_kind="deterministic",
        owner="deepdive_materializer",
    )
    dag["deepdive_dialogue"] = _node(
        depends_on=("deepdive_model",),
        producer_kind="deterministic",
        owner="deepdive_materializer",
    )
    dag["deepdive_html"] = _node(
        depends_on=("deepdive_article",),
        producer_kind="deterministic",
        owner="site_builder",
    )
    dag["deepdive_audio"] = _node(
        depends_on=("deepdive_dialogue",),
        producer_kind="deterministic",
        owner="deepdive_audio_builder",
    )
    dag["deepdive_audio_projection"] = _node(
        depends_on=("deepdive_audio",),
        producer_kind="deterministic",
        owner="deepdive_audio_builder",
    )
    dag["deepdive_video"] = _node(
        depends_on=("deepdive_audio", "deepdive_dialogue"),
        producer_kind="deterministic",
        owner="deepdive_video_builder",
    )
    dag["site_html"] = _node(
        depends_on=tuple(
            [f"digest:{category}" for category in normalized]
            + ["articles_jsonl", "summary", "daily_audio_projection"]
        ),
        producer_kind="deterministic",
        owner="site_builder",
    )
    public_inputs = tuple(
        [
            "site_html",
            "daily_video",
            "deepdive_article",
            "deepdive_html",
            "deepdive_audio_projection",
            "deepdive_video",
        ]
    )
    dag["distribution_manifest"] = _node(
        depends_on=public_inputs,
        producer_kind="deterministic",
        owner="release_materializer",
    )
    dag["publish_status"] = _node(
        depends_on=("distribution_manifest",),
        producer_kind="deterministic",
        owner="release_materializer",
    )
    dag["git_pages"] = _node(
        depends_on=("distribution_manifest", "publish_status"),
        producer_kind="external",
        owner="git_release",
    )
    dag["youtube_daily"] = _node(
        depends_on=("daily_audio", "distribution_manifest"),
        producer_kind="external",
        owner="youtube_daily",
    )
    dag["youtube_deepdive"] = _node(
        depends_on=("deepdive_audio", "distribution_manifest"),
        producer_kind="external",
        owner="youtube_deepdive",
    )
    dag["playlist"] = _node(
        depends_on=("youtube_daily", "youtube_deepdive"),
        producer_kind="external",
        owner="youtube_playlist",
    )
    dag["notification"] = _node(
        depends_on=("git_pages", "youtube_daily", "youtube_deepdive", "playlist"),
        producer_kind="external",
        owner="notification_sender",
    )
    dag["public_verification"] = _node(
        depends_on=("git_pages", "youtube_daily", "youtube_deepdive", "playlist", "notification"),
        producer_kind="external",
        owner="consumer_public_verifier",
    )

    seen: set[str] = set()
    for artifact_id, node in dag.items():
        if node["producerKind"] not in PRODUCER_ACTION:
            raise NewsGraspRepairPlanError("NG_ARTIFACT_DAG_PRODUCER_INVALID")
        if any(dependency not in seen for dependency in node["dependsOn"]):
            raise NewsGraspRepairPlanError(
                f"NG_ARTIFACT_DAG_ORDER_INVALID:{artifact_id}"
            )
        seen.add(artifact_id)
    return dag


def failure_signature(failure: Mapping[str, Any]) -> str:
    fields = (
        str(failure.get("stage") or ""),
        str(failure.get("artifactId") or ""),
        str(failure.get("predicateId") or ""),
        str(failure.get("reasonCode") or ""),
        str(failure.get("inputHash") or ""),
    )
    if any(not item or "|" in item for item in fields):
        raise NewsGraspRepairPlanError("NG_FAILURE_SIGNATURE_INVALID")
    return "|".join(fields)


def _dirty_closure(
    dag: Mapping[str, Mapping[str, Any]],
    roots: set[str],
) -> set[str]:
    dirty = set(roots)
    changed = True
    while changed:
        changed = False
        for artifact_id, node in dag.items():
            if artifact_id not in dirty and dirty.intersection(node["dependsOn"]):
                dirty.add(artifact_id)
                changed = True
    return dirty


def build_repair_plan(
    *,
    issue_date: str,
    run_id: str,
    categories: Sequence[str],
    checkpoints: Mapping[str, Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """failure rootと非Green checkpointから最小のdirty downstreamを求める。"""

    if not issue_date or not run_id:
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_IDENTITY_INVALID")
    dag = build_daily_artifact_dag(categories)
    signatures: list[str] = []
    dirty_roots: set[str] = set()
    for failure in failures:
        signature = failure_signature(failure)
        artifact_id = str(failure["artifactId"])
        if artifact_id not in dag:
            raise NewsGraspRepairPlanError(
                f"NG_REPAIR_PLAN_ARTIFACT_UNKNOWN:{artifact_id}"
            )
        signatures.append(signature)
        dirty_roots.add(artifact_id)
    for artifact_id in dag:
        checkpoint = checkpoints.get(artifact_id)
        if not isinstance(checkpoint, Mapping) or checkpoint.get("status") != "Green":
            dirty_roots.add(artifact_id)

    dirty = _dirty_closure(dag, dirty_roots)
    steps: list[dict[str, Any]] = []
    for artifact_id, node in dag.items():
        action = (
            PRODUCER_ACTION[str(node["producerKind"])]
            if artifact_id in dirty
            else "reuse"
        )
        steps.append(
            {
                "artifactId": artifact_id,
                "action": action,
                "dependsOn": list(node["dependsOn"]),
                "owner": node["owner"],
            }
        )
    active_steps = [item for item in steps if item["action"] != "reuse"]
    dirty_reporters = sum(
        1
        for item in active_steps
        if item["action"] == "repair_model"
        and str(item["artifactId"]).startswith("reporter:")
    )
    other_model_calls = sum(
        1
        for item in active_steps
        if item["action"] == "repair_model"
        and not str(item["artifactId"]).startswith("reporter:")
    )
    plan: dict[str, Any] = {
        "schemaVersion": REPAIR_PLAN_SCHEMA,
        "issueDate": issue_date,
        "runId": run_id,
        "categories": list(categories),
        "status": "repair_required" if active_steps else "completed",
        "failureSignatures": signatures,
        "dirtyRoots": [item for item in dag if item in dirty_roots],
        "dirtyArtifacts": [item for item in dag if item in dirty],
        "nextArtifactId": active_steps[0]["artifactId"] if active_steps else "completed",
        "modelCallsRequired": min(3, dirty_reporters) + other_model_calls,
        "steps": steps,
    }
    plan["planSha256"] = hashlib.sha256(_canonical(plan)).hexdigest()
    return plan


def validate_repair_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schemaVersion") != REPAIR_PLAN_SCHEMA:
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SCHEMA_INVALID")
    body = dict(plan)
    expected = str(body.pop("planSha256", ""))
    if expected != hashlib.sha256(_canonical(body)).hexdigest():
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_HASH_INVALID")
    if set(plan) != {
        "schemaVersion",
        "issueDate",
        "runId",
        "categories",
        "status",
        "failureSignatures",
        "dirtyRoots",
        "dirtyArtifacts",
        "nextArtifactId",
        "modelCallsRequired",
        "steps",
        "planSha256",
    }:
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SHAPE_INVALID")
    issue_date = plan.get("issueDate")
    run_id = plan.get("runId")
    categories = plan.get("categories")
    if (
        not isinstance(issue_date, str)
        or not issue_date
        or not isinstance(run_id, str)
        or not run_id
        or not isinstance(categories, list)
    ):
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SHAPE_INVALID")
    dag = build_daily_artifact_dag(categories)
    dag_order = list(dag)
    failures = plan.get("failureSignatures")
    dirty_roots = plan.get("dirtyRoots")
    dirty_artifacts = plan.get("dirtyArtifacts")
    steps = plan.get("steps")
    if not all(isinstance(item, list) for item in (failures, dirty_roots, dirty_artifacts, steps)):
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SHAPE_INVALID")
    # 保存済みの旧台本依存だけは、当時のDAGで全体の意味検証を行う。
    # 新規planはbuild_daily_artifact_dagから現行依存で生成される。
    if any(isinstance(step, Mapping) and step.get("artifactId") == "daily_audio_script"
           and step.get("dependsOn") == ["summary"] for step in steps):
        dag["daily_audio_script"]["dependsOn"] = ["summary"]
    if len(set(dirty_roots)) != len(dirty_roots) or any(item not in dag for item in dirty_roots):
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
    expected_roots = [item for item in dag_order if item in set(dirty_roots)]
    expected_dirty_set = _dirty_closure(dag, set(dirty_roots))
    expected_dirty = [item for item in dag_order if item in expected_dirty_set]
    if dirty_roots != expected_roots or dirty_artifacts != expected_dirty:
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
    failure_roots: list[str] = []
    for signature in failures:
        if not isinstance(signature, str):
            raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
        parts = signature.split("|")
        if len(parts) != 5 or any(not part for part in parts) or parts[1] not in dag:
            raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
        failure_roots.append(parts[1])
    if any(root not in dirty_roots for root in failure_roots):
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
    expected_steps = [
        {
            "artifactId": artifact_id,
            "action": (
                PRODUCER_ACTION[str(node["producerKind"])]
                if artifact_id in expected_dirty_set
                else "reuse"
            ),
            "dependsOn": list(node["dependsOn"]),
            "owner": node["owner"],
        }
        for artifact_id, node in dag.items()
    ]
    if steps != expected_steps:
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
    active_steps = [item for item in expected_steps if item["action"] != "reuse"]
    dirty_reporters = sum(
        1
        for item in active_steps
        if item["action"] == "repair_model"
        and str(item["artifactId"]).startswith("reporter:")
    )
    other_model_calls = sum(
        1
        for item in active_steps
        if item["action"] == "repair_model"
        and not str(item["artifactId"]).startswith("reporter:")
    )
    if (
        plan.get("status") != ("repair_required" if active_steps else "completed")
        or plan.get("nextArtifactId")
        != (active_steps[0]["artifactId"] if active_steps else "completed")
        or plan.get("modelCallsRequired") != min(3, dirty_reporters) + other_model_calls
    ):
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_SEMANTIC_INVALID")
    return dict(plan)


def write_repair_plan(path: Path | str, plan: Mapping[str, Any]) -> None:
    validated = validate_repair_plan(plan)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
            mode="wb",
        ) as stream:
            temporary = Path(stream.name)
            stream.write(_canonical(validated) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_repair_plan(path: Path | str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_INVALID") from exc
    if not isinstance(value, Mapping):
        raise NewsGraspRepairPlanError("NG_REPAIR_PLAN_INVALID")
    return validate_repair_plan(value)


class ModelCallBudgetLedger:
    """同一Daily runの初回callと修復callを共有atomic台帳で制限する。"""

    def __init__(
        self,
        path: Path | str,
        *,
        issue_date: str,
        run_id: str,
        initial_limit: int = MAX_INITIAL_MODEL_CALLS,
        repair_limit: int = MAX_REPAIR_MODEL_CALLS,
    ) -> None:
        if (
            not issue_date
            or not run_id
            or initial_limit < 0
            or initial_limit > MAX_INITIAL_MODEL_CALLS
            or repair_limit < 0
            or repair_limit > MAX_REPAIR_MODEL_CALLS
        ):
            raise NewsGraspRepairPlanError("NG_MODEL_CALL_BUDGET_INVALID")
        self.path = Path(path)
        self.issue_date = issue_date
        self.run_id = run_id
        self.initial_limit = initial_limit
        self.repair_limit = repair_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _lock(self) -> Path:
        lock = self.path.with_name(f".{self.path.name}.lock")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError as exc:
                try:
                    age = time.time() - lock.stat().st_mtime
                except OSError:
                    age = 0
                if age > 300:
                    lock.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise NewsGraspRepairPlanError("NG_MODEL_CALL_BUDGET_BUSY") from exc
                time.sleep(0.01)
        try:
            os.write(descriptor, f"{os.getpid()}|{time.time()}".encode("ascii"))
        finally:
            os.close(descriptor)
        return lock

    def _empty(self) -> dict[str, Any]:
        return {
            "schemaVersion": "NEWS_GRASP_MODEL_CALL_BUDGET_V1",
            "issueDate": self.issue_date,
            "runId": self.run_id,
            "limits": {
                "initial": self.initial_limit,
                "repair": self.repair_limit,
            },
            "calls": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise NewsGraspRepairPlanError("NG_MODEL_CALL_BUDGET_CORRUPT") from exc
        expected = self._empty()
        if (
            not isinstance(value, dict)
            or value.get("schemaVersion") != expected["schemaVersion"]
            or value.get("issueDate") != self.issue_date
            or value.get("runId") != self.run_id
            or value.get("limits") != expected["limits"]
            or not isinstance(value.get("calls"), dict)
        ):
            raise NewsGraspRepairPlanError("NG_MODEL_CALL_BUDGET_IDENTITY_MISMATCH")
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
                mode="wb",
            ) as stream:
                temporary = Path(stream.name)
                stream.write(_canonical(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def consume(
        self,
        *,
        call_id: str,
        budget_class: str,
        artifact_id: str,
        input_hash: str,
    ) -> dict[str, Any]:
        if (
            budget_class not in {"initial", "repair"}
            or not call_id
            or not artifact_id
            or not input_hash
        ):
            raise NewsGraspRepairPlanError("NG_MODEL_CALL_BUDGET_REQUEST_INVALID")
        lock = self._lock()
        try:
            ledger = self._load()
            calls = ledger["calls"]
            request = {
                "budgetClass": budget_class,
                "artifactId": artifact_id,
                "inputHash": input_hash,
            }
            existing = calls.get(call_id)
            if existing is not None:
                if existing != request:
                    raise NewsGraspRepairPlanError("NG_MODEL_CALL_IDEMPOTENCY_CONFLICT")
                return {
                    "schemaVersion": "NEWS_GRASP_MODEL_CALL_BUDGET_RECEIPT_V1",
                    "ok": True,
                    "consumed": False,
                    "idempotent": True,
                    "callId": call_id,
                    **request,
                }
            used = sum(
                1
                for item in calls.values()
                if isinstance(item, Mapping)
                and item.get("budgetClass") == budget_class
            )
            limit = self.initial_limit if budget_class == "initial" else self.repair_limit
            if used >= limit:
                code = (
                    "NG_MODEL_CALL_INITIAL_BUDGET_EXHAUSTED"
                    if budget_class == "initial"
                    else "NG_MODEL_CALL_REPAIR_BUDGET_EXHAUSTED"
                )
                raise NewsGraspRepairPlanError(code)
            calls[call_id] = request
            self._write(ledger)
            return {
                "schemaVersion": "NEWS_GRASP_MODEL_CALL_BUDGET_RECEIPT_V1",
                "ok": True,
                "consumed": True,
                "idempotent": False,
                "callId": call_id,
                "used": used + 1,
                "limit": limit,
                **request,
            }
        finally:
            lock.unlink(missing_ok=True)

    def usage(self) -> dict[str, int]:
        """現在の共有台帳を読み、class別の実消費数を返す。"""

        lock = self._lock()
        try:
            calls = self._load()["calls"].values()
            initial = sum(
                1
                for item in calls
                if isinstance(item, Mapping) and item.get("budgetClass") == "initial"
            )
            repair = sum(
                1
                for item in calls
                if isinstance(item, Mapping) and item.get("budgetClass") == "repair"
            )
            return {"initial": initial, "repair": repair, "total": initial + repair}
        finally:
            lock.unlink(missing_ok=True)


__all__ = [
    "NewsGraspRepairPlanError",
    "ModelCallBudgetLedger",
    "REPAIR_PLAN_SCHEMA",
    "build_daily_artifact_dag",
    "build_repair_plan",
    "failure_signature",
    "load_repair_plan",
    "validate_repair_plan",
    "write_repair_plan",
]
