#!/usr/bin/env python3
"""現行 News-Grasp モデルを Luna-high へ置換できるか判定する単一HTMLレポートを生成する。"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from tools import run_codex_recovery_benchmark as recovery_benchmark
    from tools import run_external_benchmark_matrix as external_benchmark
except ModuleNotFoundError:  # direct script execution
    import run_codex_recovery_benchmark as recovery_benchmark
    import run_external_benchmark_matrix as external_benchmark


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_RECOVERY_MODELS = ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4")
BASE_EXTERNAL_MODELS = ("GPT-5.5", "GPT-5.6 Terra", "GPT-5.6 Luna", "GPT-5.4")
EFFORTS = ("low", "medium", "high")
REPETITIONS = (1, 2, 3)
RECOVERY_BASE_SHA256 = "e783334ed76b4cef5c716c5c162beda57381edd9f4cb344123cc8ecdb534c528"
EXTERNAL_BASE_SHA256 = "0e97f7869e94f99c1d9e7355dab7962de2cbbd79b6962590a58c906d3b8ab3bd"
OFFICIAL_RATE_CARD = "https://help.openai.com/en/articles/20001106-codex-rate-card"


def portable_provenance_path(path: Path) -> str:
    """公開可能な provenance として repo 相対パスだけを返す。"""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"external/{resolved.name}"

ROLE_AXES: dict[str, dict[str, tuple[str, ...]]] = {
    "reporter": {"external": ("JA_NLU", "JA_SUMMARY"), "recovery": ("NG-RC", "NG-OPS")},
    "editor": {"external": ("JA_NLU", "JA_SUMMARY"), "recovery": ()},
    "repair": {"external": ("CODE_REPAIR", "CODE_SYNTH"), "recovery": ("NG-MF", "NG-PATCH", "NG-CODE")},
    "newsroom_editor": {
        "external": ("CODE_REPAIR", "JA_NLU", "JA_SUMMARY"),
        "recovery": ("NG-RC", "NG-MF", "NG-PATCH", "NG-LONG", "NG-OPS"),
    },
    "deepdive": {"external": ("JA_NLU", "JA_SUMMARY"), "recovery": ("NG-LONG", "NG-OPS")},
}

ROLE_LABELS = {
    "reporter": "記事生成 reporter",
    "editor": "文体 editor",
    "repair": "復旧 repair worker",
    "newsroom_editor": "Newsroom editor",
    "deepdive": "DeepDive",
}


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError(f"records[] required: {path}")
    return [dict(record) for record in records]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_key(record: dict[str, Any]) -> tuple[str, str, str, int]:
    return (
        str(record.get("model") or ""),
        str(record.get("effort") or ""),
        str(record.get("case_id") or ""),
        int(record.get("repetition") or 0),
    )


def validate_record_set(
    records: list[dict[str, Any]], *, expected_keys: set[tuple[str, str, str, int]]
) -> dict[str, Any]:
    keys = [_record_key(record) for record in records]
    counts = Counter(keys)
    duplicates = [list(key) for key, count in counts.items() if count > 1]
    missing = [list(key) for key in sorted(expected_keys - set(keys))]
    unexpected = [list(key) for key in sorted(set(keys) - expected_keys)]
    rejected_rows = [index for index, record in enumerate(records) if bool(record.get("rejected")) or not all(_record_key(record))]
    if duplicates:
        raise ValueError(f"duplicate benchmark key: {duplicates[:3]}")
    if rejected_rows:
        raise ValueError(f"rejected benchmark row: {rejected_rows[:3]}")
    if missing or unexpected:
        raise ValueError(f"incomplete benchmark input: missing={len(missing)} unexpected={len(unexpected)}")
    return {
        "complete": True,
        "record_count": len(records),
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "rejected_rows": rejected_rows,
    }


def _expected_keys(models: Iterable[str], efforts: Iterable[str], case_ids: Iterable[str]) -> set[tuple[str, str, str, int]]:
    return {
        (model, effort, case_id, repetition)
        for model in models
        for effort in efforts
        for case_id in case_ids
        for repetition in REPETITIONS
    }


def validate_input_files(
    *,
    recovery_base: Path,
    external_base: Path,
    recovery_sol: Path | None = None,
    external_sol: Path | None = None,
) -> dict[str, Any]:
    recovery_cases = [case["case_id"] for case in recovery_benchmark.build_execution_cases()]
    external_cases = [case["case_id"] for case in external_benchmark.build_matrix_cases()]
    actual_recovery_hash = sha256_file(recovery_base)
    actual_external_hash = sha256_file(external_base)
    if actual_recovery_hash != RECOVERY_BASE_SHA256:
        raise ValueError(f"recovery baseline hash mismatch: {actual_recovery_hash}")
    if actual_external_hash != EXTERNAL_BASE_SHA256:
        raise ValueError(f"external baseline hash mismatch: {actual_external_hash}")
    result: dict[str, Any] = {
        "schema_version": "luna_high_reuse_manifest.v1",
        "recovery_base": {
            "path": portable_provenance_path(recovery_base),
            "sha256": actual_recovery_hash,
            "validation": validate_record_set(
                load_records(recovery_base),
                expected_keys=_expected_keys(BASE_RECOVERY_MODELS, EFFORTS, recovery_cases),
            ),
        },
        "external_base": {
            "path": portable_provenance_path(external_base),
            "sha256": actual_external_hash,
            "validation": validate_record_set(
                load_records(external_base),
                expected_keys=_expected_keys(BASE_EXTERNAL_MODELS, EFFORTS, external_cases),
            ),
        },
    }
    if recovery_sol:
        result["recovery_sol"] = {
            "path": portable_provenance_path(recovery_sol),
            "sha256": sha256_file(recovery_sol),
            "validation": validate_record_set(
                load_records(recovery_sol),
                expected_keys=_expected_keys(("gpt-5.6-sol",), ("high",), recovery_cases),
            ),
        }
    if external_sol:
        result["external_sol"] = {
            "path": portable_provenance_path(external_sol),
            "sha256": sha256_file(external_sol),
            "validation": validate_record_set(
                load_records(external_sol),
                expected_keys=_expected_keys(("GPT-5.6 Sol",), ("high",), external_cases),
            ),
        }
    result["complete"] = all(item["validation"]["complete"] for key, item in result.items() if key not in {"schema_version", "complete"})
    return result


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.mean(values) if values else 0.0


def _stdev(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.stdev(values) if len(values) > 1 else 0.0


def _external_quality(record: dict[str, Any]) -> float:
    if record.get("quality_score_1_to_5") is not None:
        return float(record["quality_score_1_to_5"]) / 5.0
    return max(0.0, min(1.0, float(record.get("score") or 0.0) / 10.0))


def _recovery_success(record: dict[str, Any]) -> float:
    task_id = str(record.get("task_id"))
    field = {
        "NG-RC": "root_cause_correct",
        "NG-MF": "minimal_fix",
        "NG-PATCH": "verified_closure",
        "NG-LONG": "verified_closure",
        "NG-OPS": "ops_stable",
        "NG-CODE": "coding_pass",
    }.get(task_id, "verified_closure")
    return 1.0 if bool(record.get(field)) else 0.0


def _normalize_model(model: str) -> str:
    return model.casefold().replace("gpt-", "gpt-").replace(" ", "-")


def _slice_records(records: list[dict[str, Any]], *, model: str, effort: str, tasks: Iterable[str], task_field: str) -> list[dict[str, Any]]:
    task_set = set(tasks)
    normalized = _normalize_model(model)
    return [
        record
        for record in records
        if _normalize_model(str(record.get("model"))) == normalized
        and str(record.get("effort")) == effort
        and str(record.get(task_field)) in task_set
    ]


def role_metrics(
    *,
    role: str,
    model: str,
    effort: str,
    recovery_records: list[dict[str, Any]],
    external_records: list[dict[str, Any]],
) -> dict[str, Any]:
    axes = ROLE_AXES[role]
    external = _slice_records(external_records, model=model, effort=effort, tasks=axes["external"], task_field="task_type")
    recovery = _slice_records(recovery_records, model=model, effort=effort, tasks=axes["recovery"], task_field="task_id")
    quality_values = [_external_quality(record) for record in external] + [_recovery_success(record) for record in recovery]
    pass_values = [1.0 if bool(record.get("pass")) else 0.0 for record in external] + [_recovery_success(record) for record in recovery]
    closure_values = [_recovery_success(record) for record in recovery]
    all_records = external + recovery
    return {
        "quality": round(_mean(quality_values), 6),
        "quality_stdev": round(_stdev(quality_values), 6),
        "pass_rate": round(_mean(pass_values), 6),
        "fatal_rate": round(_mean(1.0 if bool(record.get("fatal")) else 0.0 for record in all_records), 6),
        "closure_rate": round(_mean(closure_values) if recovery else _mean(pass_values), 6),
        "sample_count": len(all_records),
        "credits": round(sum(float(record.get("credits") or 0.0) for record in all_records), 6),
        "messages": sum(int(record.get("messages") or record.get("usage", {}).get("messages") or 0) for record in all_records),
    }


def classify_role_replacement(*, current: dict[str, Any], luna_high: dict[str, Any], current_effort: str | None) -> dict[str, Any]:
    deltas = {
        "quality": float(luna_high["quality"]) - float(current["quality"]),
        "pass_rate": float(luna_high["pass_rate"]) - float(current["pass_rate"]),
        "closure_rate": float(luna_high["closure_rate"]) - float(current["closure_rate"]),
        "fatal_rate": float(luna_high["fatal_rate"]) - float(current["fatal_rate"]),
    }
    enough_samples = int(current.get("sample_count") or 0) >= 3 and int(luna_high.get("sample_count") or 0) >= 3
    noninferior = (
        deltas["quality"] >= -0.03
        and deltas["pass_rate"] >= -0.03
        and deltas["closure_rate"] >= -0.03
        and deltas["fatal_rate"] <= 0.0
    )
    if not enough_samples or current_effort is None:
        verdict = "conditional"
        reason = "現行effort未固定または比較サンプル不足のため、条件付き判断"
    elif noninferior:
        verdict = "replace_ok"
        reason = "品質・pass・closureが非劣後で、fatal増加なし"
    else:
        verdict = "keep_current"
        reason = "品質または運用ゲートで非劣後条件を満たさない"
    return {"verdict": verdict, "reason": reason, "deltas": {key: round(value, 6) for key, value in deltas.items()}}


def apply_role_measurement_guard(*, role: str, decision: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(decision)
    if role == "deepdive" and guarded["verdict"] == "replace_ok":
        guarded["verdict"] = "conditional"
        guarded["reason"] = "共通軸では非劣後だが、DeepDive専用の長文構成・洞察品質fixtureが未測定"
    return guarded


def _load_policy(path: Path) -> dict[str, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("news_grasp_model_policy_for_report", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load policy: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.DEFAULT_MODEL_POLICY)


def _model_effort_key(model: str, effort: str) -> str:
    return f"{_normalize_model(model)} / {effort}"


def _all_model_efforts(recovery_records: list[dict[str, Any]], external_records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs = {(_normalize_model(str(record.get("model"))), str(record.get("effort"))) for record in recovery_records + external_records}
    order = {name: index for index, name in enumerate(("gpt-5.4", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"))}
    effort_order = {name: index for index, name in enumerate(EFFORTS)}
    return sorted(pairs, key=lambda pair: (order.get(pair[0], 99), effort_order.get(pair[1], 99)))


def build_report_payload(
    *,
    policy_path: Path,
    recovery_records: list[dict[str, Any]],
    external_records: list[dict[str, Any]],
    reuse_manifest: dict[str, Any],
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    candidate_model = "gpt-5.6-luna"
    candidate_effort = "high"
    role_rows = []
    for role in ROLE_AXES:
        role_policy = policy[role]
        current_model = str(role_policy["default"])
        current_effort = str(role_policy["reasoning"]) if role_policy.get("reasoning") else None
        current_metric_effort = current_effort or "medium"
        current = role_metrics(
            role=role,
            model=current_model,
            effort=current_metric_effort,
            recovery_records=recovery_records,
            external_records=external_records,
        )
        luna = role_metrics(
            role=role,
            model=candidate_model,
            effort=candidate_effort,
            recovery_records=recovery_records,
            external_records=external_records,
        )
        decision = apply_role_measurement_guard(
            role=role,
            decision=classify_role_replacement(current=current, luna_high=luna, current_effort=current_effort),
        )
        role_rows.append(
            {
                "role": role,
                "role_label": ROLE_LABELS[role],
                "current_model": current_model,
                "current_effort": current_effort,
                "current": f"{current_model} / {current_effort or 'unset (medium proxy)'}",
                "candidate": f"{candidate_model} / {candidate_effort}",
                "verdict": decision["verdict"],
                "reason": decision["reason"],
                "deltas": decision["deltas"],
                "current_metrics": current,
                "candidate_metrics": luna,
                "metrics": {"current": current["quality"], "candidate": luna["quality"]},
                "axes": ROLE_AXES[role],
            }
        )

    pairs = _all_model_efforts(recovery_records, external_records)
    labels = {_model_effort_key(model, effort): f"M{index + 1}" for index, (model, effort) in enumerate(pairs)}
    score_rows: list[dict[str, Any]] = []
    for task in ("CODE_REPAIR", "CODE_SYNTH", "JA_NLU", "JA_SUMMARY"):
        values: dict[str, float] = {}
        for model, effort in pairs:
            rows = _slice_records(external_records, model=model, effort=effort, tasks=(task,), task_field="task_type")
            values[_model_effort_key(model, effort)] = round(_mean(_external_quality(record) for record in rows), 6)
        score_rows.append({"axis": task, "group": "外部ベンチマーク型", "values": values})
    for task in ("NG-RC", "NG-MF", "NG-PATCH", "NG-LONG", "NG-OPS", "NG-CODE"):
        values = {}
        for model, effort in pairs:
            rows = _slice_records(recovery_records, model=model, effort=effort, tasks=(task,), task_field="task_id")
            values[_model_effort_key(model, effort)] = round(_mean(_recovery_success(record) for record in rows), 6)
        score_rows.append({"axis": task, "group": "News-Grasp復旧", "values": values})

    external_cases = {case["case_id"]: case for case in external_benchmark.build_matrix_cases()}
    recovery_cases = {case["case_id"]: case for case in recovery_benchmark.build_execution_cases()}
    case_rows = [
        {
            "case_id": case_id,
            "purpose": f"{case['task_type']}: {', '.join(case.get('difficulty_features', [])[:2])}",
            "source": ", ".join(case.get("external_source_ids", [])),
        }
        for case_id, case in external_cases.items()
    ] + [
        {
            "case_id": case_id,
            "purpose": f"{case['task_id']}: {case.get('description', case.get('input_fixture', ''))}",
            "source": "News-Grasp historical / seeded fixture",
        }
        for case_id, case in recovery_cases.items()
    ]
    all_records = recovery_records + external_records
    return {
        "schema_version": "luna_high_replacement_report.v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete": bool(reuse_manifest.get("complete")),
        "model_labels": labels,
        "roles": role_rows,
        "score_rows": score_rows,
        "case_rows": case_rows,
        "audits": [
            {"name": "3反復 coverage", "status": "pass", "detail": f"全 {len(all_records)} records の複合キーを検証"},
            {"name": "baseline hash lock", "status": "pass", "detail": "既存900 recordsをSHA-256で固定して再利用"},
            {"name": "fatal gate分離", "status": "pass", "detail": "品質平均でfatalを相殺せず、代替判定の独立条件に使用"},
            {"name": "effort分離", "status": "pass", "detail": "Luna low/medium/highを別系列として表示"},
        ],
        "measurement_limits": [
            "News-Grasp固有DeepDive品質はproxy評価。DeepDive専用の長文構成・洞察品質fixtureではない。",
            "Terraのnewsroom_editor設定はreasoning未指定。mediumを可視化proxyに使うが、代替可否はconditionalとする。",
            "Codex CLIのusage eventが無いrunは文字数推計tokenを含むため、creditは品質結論ではなく運用参考値。",
            "外部benchmark名は問題設計の根拠であり、公式leaderboard scoreの再現値ではない。",
        ],
        "reuse_manifest": reuse_manifest,
        "official_rate_card": OFFICIAL_RATE_CARD,
    }


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _bar(
    label: str,
    value: float,
    *,
    color: str = "var(--cyan)",
    detail: str = "",
    label_is_html: bool = False,
) -> str:
    width = max(0.0, min(100.0, value * 100.0))
    label_markup = label if label_is_html else _esc(label)
    return (
        '<div class="bar-row">'
        f'<div class="bar-label">{label_markup}</div>'
        '<div class="bar-track">'
        f'<div class="bar-fill" style="width:{width:.2f}%;background:{color}"></div>'
        '</div>'
        f'<div class="bar-value">{_pct(value)}{_esc(detail)}</div>'
        '</div>'
    )


def render_report(payload: dict[str, Any], output_path: Path) -> Path:
    if payload.get("complete") is not True:
        raise ValueError("complete benchmark coverage required before report")
    labels = payload["model_labels"]
    role_rows = []
    verdict_counts = Counter(row["verdict"] for row in payload["roles"])
    verdict_labels = {"replace_ok": "代替可", "conditional": "条件付き", "keep_current": "現行維持"}
    verdict_classes = {"replace_ok": "ok", "conditional": "warn", "keep_current": "stop"}
    for row in payload["roles"]:
        current_metrics = row.get("current_metrics", {})
        candidate_metrics = row.get("candidate_metrics", {})
        current_quality = float(row.get("metrics", {}).get("current", current_metrics.get("quality", 0.0)))
        candidate_quality = float(row.get("metrics", {}).get("candidate", candidate_metrics.get("quality", 0.0)))
        role_rows.append(
            '<tr class="decision-row">'
            f'<td><strong>{_esc(row.get("role_label", row["role"]))}</strong><small>{_esc(", ".join(row.get("axes", {}).get("external", ())))}</small></td>'
            f'<td>{_esc(row["current"])}</td><td>{_esc(row["candidate"])}</td>'
            f'<td><span class="verdict {verdict_classes[row["verdict"]]}">{verdict_labels[row["verdict"]]}</span></td>'
            f'<td>{_bar("現行", current_quality, color="var(--navy-2)")}{_bar("Luna-high", candidate_quality, color="var(--gold)")}</td>'
            f'<td>{_esc(row["reason"])}</td>'
            '</tr>'
        )
    explorer = []
    for axis in payload["score_rows"]:
        bars = []
        for key, value in axis["values"].items():
            model_color = "var(--gold)" if "luna / high" in key else "var(--cyan)" if "luna" in key else "var(--navy-2)"
            code = labels.get(key, "?")
            label_markup = f'<span data-code-label>{_esc(code)}</span><span data-full-label>{_esc(key)}</span>'
            bars.append(_bar(label_markup, float(value), color=model_color, label_is_html=True))
        explorer.append(f'<section class="axis-block"><div><p class="eyebrow">{_esc(axis.get("group", ""))}</p><h3>{_esc(axis["axis"])}</h3></div><div class="bars">{"".join(bars)}</div></section>')
    winners = []
    for axis in payload["score_rows"]:
        if not axis["values"]:
            continue
        winner, score = max(axis["values"].items(), key=lambda item: item[1])
        winners.append(
            '<tr>'
            f'<td>{_esc(axis["axis"])}</td><td><span class="model-code">{_esc(labels.get(winner, "?"))}</span> {_esc(winner)}</td>'
            f'<td>{_bar("score", float(score), color="var(--gold)")}</td>'
            '</tr>'
        )
    operational = []
    for row in payload["roles"]:
        current = row.get("current_metrics", {})
        candidate = row.get("candidate_metrics", {})
        operational.append(
            '<section class="op-row">'
            f'<div><h3>{_esc(row.get("role_label", row["role"]))}</h3><p>{_esc(row["current"])} → {_esc(row["candidate"])}</p></div>'
            f'<div>{_bar("Pass 現行", float(current.get("pass_rate", 0.0)), color="var(--navy-2)")}{_bar("Pass Luna-high", float(candidate.get("pass_rate", 0.0)), color="var(--gold)")}</div>'
            f'<div>{_bar("Closure 現行", float(current.get("closure_rate", 0.0)), color="var(--navy-2)")}{_bar("Closure Luna-high", float(candidate.get("closure_rate", 0.0)), color="var(--gold)")}</div>'
            f'<div class="fatal-cell">Fatal: 現行 {_pct(float(current.get("fatal_rate", 0.0)))} / Luna-high {_pct(float(candidate.get("fatal_rate", 0.0)))}</div>'
            '</section>'
        )
    case_rows = ''.join(
        f'<tr><td><code>{_esc(row["case_id"])}</code></td><td>{_esc(row["purpose"])}</td><td>{_esc(row.get("source", ""))}</td></tr>'
        for row in payload["case_rows"]
    )
    audit_rows = ''.join(
        f'<tr><td>{_esc(row["name"])}</td><td><span class="verdict ok">{_esc(row["status"])}</span></td><td>{_esc(row["detail"])}</td></tr>'
        for row in payload["audits"]
    )
    label_legend = ''.join(
        f'<span><b data-code-label>{_esc(label)}</b><b data-full-label>{_esc(key)}</b></span>'
        for key, label in labels.items()
    )
    limits = ''.join(f'<li>{_esc(item)}</li>' for item in payload["measurement_limits"])
    overall = "Luna-highは役割別に選択導入" if verdict_counts["replace_ok"] else "Luna-high全面代替は見送り"
    report = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>News-Grasp Luna-high 代替評価</title>
<style>
:root{{--navy:#181C2A;--navy-2:#34445f;--gold:#C9A155;--paper:#FAF7F0;--white:#fff;--ink:#202532;--muted:#667085;--line:#d9d4ca;--cyan:#2D728F;--green:#2f7658;--red:#9b352f;--amber:#9a6a1f}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Yu Gothic UI","Meiryo",sans-serif;letter-spacing:0;line-height:1.65}}a{{color:var(--cyan)}}button{{font:inherit}}.wrap{{width:min(1500px,calc(100% - 40px));margin:auto}}header{{background:var(--navy);color:var(--white);padding:46px 0 34px;border-bottom:6px solid var(--gold)}}.eyebrow{{margin:0 0 6px;color:var(--gold);font-size:12px;font-weight:800;text-transform:uppercase}}h1{{font-size:clamp(30px,4vw,56px);line-height:1.15;margin:0 0 14px}}h2{{font-size:28px;line-height:1.25;margin:0 0 18px}}h3{{font-size:18px;margin:0}}p{{margin:0}}.lead{{max-width:980px;font-size:18px;color:#e6e9ef}}.hero-grid{{display:grid;grid-template-columns:2fr repeat(3,1fr);gap:12px;margin-top:28px}}.hero-verdict,.hero-stat{{border:1px solid #4b5365;padding:18px;background:#222839;border-radius:4px}}.hero-verdict strong{{display:block;color:var(--gold);font-size:24px}}.hero-stat b{{display:block;font-size:30px}}.toolbar{{position:sticky;top:0;z-index:5;background:rgba(250,247,240,.96);border-bottom:1px solid var(--line);padding:10px 0}}.toolbar .wrap{{display:flex;align-items:center;justify-content:space-between;gap:16px}}.toolbar nav{{display:flex;gap:14px;overflow:auto;white-space:nowrap}}.toggle{{border:1px solid var(--navy);background:var(--white);padding:7px 12px;border-radius:4px;cursor:pointer}}main section.band{{padding:48px 0;border-bottom:1px solid var(--line)}}.section-head{{display:grid;grid-template-columns:1fr 1fr;gap:30px;align-items:end;margin-bottom:22px}}.section-head p{{color:var(--muted)}}.table-wrap{{overflow:auto;background:var(--white);border:1px solid var(--line)}}table{{border-collapse:collapse;width:100%;min-width:920px}}th,td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#ece8df;color:var(--navy);font-size:12px}}td small{{display:block;color:var(--muted);margin-top:4px}}.decision-matrix{{table-layout:fixed}}.decision-matrix th:first-child{{width:16%}}.verdict{{display:inline-block;padding:3px 8px;border-radius:3px;color:white;font-size:12px;font-weight:800;white-space:nowrap}}.verdict.ok{{background:var(--green)}}.verdict.warn{{background:var(--amber)}}.verdict.stop{{background:var(--red)}}.bar-row{{display:grid;grid-template-columns:minmax(128px,230px) minmax(160px,1fr) 72px;gap:10px;align-items:center;margin:7px 0;min-height:24px}}.bar-label{{font-size:12px;overflow-wrap:anywhere}}.bar-track{{height:14px;background:#e4e1db;border:1px solid #d2cec5;position:relative;overflow:hidden}}.bar-fill{{height:100%;min-width:1px}}.bar-value{{font-variant-numeric:tabular-nums;font-size:12px;text-align:right}}.axis-block{{display:grid;grid-template-columns:210px 1fr;gap:20px;padding:24px 0;border-top:1px solid var(--line)}}.axis-block:first-child{{border-top:0}}.score-surface{{background:var(--white);border:1px solid var(--line);padding:12px 24px}}.legend{{display:flex;flex-wrap:wrap;gap:8px 18px;margin-bottom:18px;font-size:12px}}.legend span{{display:inline-flex;gap:6px}}.model-code{{display:inline-block;background:var(--navy);color:white;padding:2px 6px;border-radius:3px;font-weight:800}}.op-row{{display:grid;grid-template-columns:1.1fr 1.5fr 1.5fr .8fr;gap:18px;align-items:center;padding:20px 0;border-top:1px solid var(--line)}}.fatal-cell{{font-size:13px;font-weight:700}}.note-list{{margin:0;padding-left:22px;display:grid;gap:12px}}.method-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.method{{background:var(--white);border-left:4px solid var(--gold);padding:18px}}code{{font-family:Consolas,monospace;font-size:12px}}footer{{background:var(--navy);color:#dbe0e8;padding:32px 0}}body[data-label-mode="code"] [data-full-label]{{display:none}}body[data-label-mode="full"] [data-code-label]{{display:none}}
.label-toggle{{display:flex;border:1px solid var(--navy);border-radius:4px;overflow:hidden}}.label-toggle button{{border:0;border-right:1px solid var(--navy);background:var(--white);padding:7px 12px;cursor:pointer}}.label-toggle button:last-child{{border-right:0}}
@media(max-width:900px){{.wrap{{width:min(100% - 24px,1500px)}}.hero-grid{{grid-template-columns:1fr 1fr}}.hero-verdict{{grid-column:1/-1}}.section-head,.axis-block,.op-row{{grid-template-columns:1fr}}.method-grid{{grid-template-columns:1fr}}.toolbar nav{{display:none}}.bar-row{{grid-template-columns:110px minmax(120px,1fr) 60px}}}}
@media print{{.toolbar{{display:none}}header{{padding:24px 0}}main section.band{{padding:28px 0}}}}
</style></head>
<body data-label-mode="code">
<header><div class="wrap"><p class="eyebrow">Hero verdict / 2026-07-15 / 3 repetitions</p><h1>News-Grasp Luna-high 代替評価</h1><p class="lead">現行全モデルをeffort別に分離し、外部benchmark型8ケースとNews-Grasp復旧17ケースを同一3反復で比較。品質、closure、fatal、コストを混ぜずに役割ごとの置換可否を判定した。</p><div class="hero-grid"><div class="hero-verdict"><span>結論</span><strong>{_esc(overall)}</strong><span>全面置換ではなく、役割ごとのゲート判定を採用</span></div><div class="hero-stat"><span>代替可</span><b>{verdict_counts['replace_ok']}</b></div><div class="hero-stat"><span>条件付き</span><b>{verdict_counts['conditional']}</b></div><div class="hero-stat"><span>現行維持</span><b>{verdict_counts['keep_current']}</b></div></div></div></header>
<div class="toolbar"><div class="wrap"><nav><a href="#decision">判断</a><a href="#scores">スコア</a><a href="#ops">運用</a><a href="#limits">限界</a><a href="#cases">ケース</a></nav><div class="label-toggle" aria-label="モデル表示"><button data-mode="symbol" type="button">M番号</button><button data-mode="name" type="button">モデル名</button></div></div></div>
<main>
<section class="band" id="decision"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Decision Matrix</p><h2>役割別の置換判断</h2></div><p>現行設定とLuna-highの同じ役割軸だけを比較。品質差 -3pt以内、pass/closure非劣後、fatal増加なしを置換条件とした。</p></div><div class="table-wrap"><table class="decision-matrix"><thead><tr><th>役割</th><th>現行</th><th>候補</th><th>判断</th><th>品質比較</th><th>根拠</th></tr></thead><tbody>{''.join(role_rows)}</tbody></table></div></div></section>
<section class="band" id="scores"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Score Explorer</p><h2>全モデル × effort × 評価軸</h2></div><p>同名モデルでもeffortを別系列として表示。棒の長さは0-100%へ正規化し、数値を併記する。</p></div><div class="legend">{label_legend}</div><div class="score-surface score-explorer" data-report-primary="true">{''.join(explorer)}</div></div></section>
<section class="band"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Usecase Winners</p><h2>評価軸ごとの首位</h2></div><p>総合点だけで採用せず、用途別に最も高いモデル・effortを示す。同点は配列順の代表値。</p></div><div class="table-wrap"><table><thead><tr><th>用途</th><th>首位</th><th>スコア</th></tr></thead><tbody>{''.join(winners)}</tbody></table></div></div></section>
<section class="band" id="ops"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Operational Gate</p><h2>pass・closure・fatal</h2></div><p>品質平均で運用失敗を相殺しない。fatalは独立した拒否条件として判定する。</p></div>{''.join(operational)}</div></section>
<section class="band" id="limits"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Measurement Limit</p><h2>この調査で断言できないこと</h2></div><p>観測限界を明示し、proxyを実タスクの確証へすり替えない。</p></div><ul class="note-list">{limits}</ul></div></section>
<section class="band" data-report-section="score-method"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Evaluation Design</p><h2>過去調査から継承した方法</h2></div><p>ローカルLLM比較で用いた3反復、機能テスト、fatal/cap/minor境界、raw evidence、識別力監査をCodex実務へ移植した。</p></div><div class="method-grid"><div class="method"><h3>再現性と重み</h3><p>全ケース3反復。model / effort / case / repetitionの複合キーをhash lockし、役割ごとに同じcase重みで平均した。</p></div><div class="method"><h3>品質と日本語品質</h3><p>codingは実sandbox編集とpytest、日本語品質はNLUと要約のsource-grounded oracle、形式制御はJSONと禁止事項で測定した。</p></div><div class="method"><h3>安定性・速度・コスト</h3><p>運用安定性、速度、creditコストを品質と別軸化。公式rate cardはcostだけに使用した。VRAMはクラウド実行モデルのため非適用とした。</p></div></div><p style="margin-top:18px"><a href="{_esc(payload.get('official_rate_card', OFFICIAL_RATE_CARD))}">OpenAI Codex rate card</a></p></div></section>
<section class="band" id="cases"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Case Library</p><h2>25ケースの目的と出典</h2></div><p>外部benchmark型8ケースとNews-Grasp復旧17ケース。ケースの難度と測定対象を追跡可能にする。</p></div><div class="table-wrap"><table><thead><tr><th>case_id</th><th>測定目的</th><th>出典・由来</th></tr></thead><tbody>{case_rows}</tbody></table></div></div></section>
<section class="band"><div class="wrap"><div class="section-head"><div><p class="eyebrow">Audits</p><h2>入力・採点・提示の監査</h2></div><p>再利用データのhash、3反復coverage、fatal分離、effort分離をレポート生成前に検査した。</p></div><div class="table-wrap"><table><thead><tr><th>監査</th><th>状態</th><th>証跡</th></tr></thead><tbody>{audit_rows}</tbody></table></div></div></section>
</main><footer><div class="wrap"><p>News-Grasp model policy decision support / generated {_esc(payload.get('generated_at', ''))}</p></div></footer>
<script>document.querySelectorAll('.label-toggle button').forEach((button)=>button.addEventListener('click',()=>{{document.body.dataset.labelMode=button.dataset.mode==='name'?'full':'code';}}));</script>
</body></html>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-inputs")
    validate.add_argument("--recovery-base", type=Path, required=True)
    validate.add_argument("--external-base", type=Path, required=True)
    validate.add_argument("--recovery-sol", type=Path)
    validate.add_argument("--external-sol", type=Path)
    validate.add_argument("--manifest-out", type=Path, required=True)
    render = subparsers.add_parser("render")
    render.add_argument("--policy", type=Path, required=True)
    render.add_argument("--recovery-base", type=Path, required=True)
    render.add_argument("--recovery-sol", type=Path, required=True)
    render.add_argument("--external-base", type=Path, required=True)
    render.add_argument("--external-sol", type=Path, required=True)
    render.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = validate_input_files(
        recovery_base=args.recovery_base,
        external_base=args.external_base,
        recovery_sol=getattr(args, "recovery_sol", None),
        external_sol=getattr(args, "external_sol", None),
    )
    if args.command == "validate-inputs":
        _json_dump(args.manifest_out, manifest)
        return 0
    recovery_records = load_records(args.recovery_base) + load_records(args.recovery_sol)
    external_records = load_records(args.external_base) + load_records(args.external_sol)
    payload = build_report_payload(
        policy_path=args.policy,
        recovery_records=recovery_records,
        external_records=external_records,
        reuse_manifest=manifest,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(args.out_dir / "summary.json", payload)
    render_report(payload, args.out_dir / "luna-high-replacement-report.html")
    _json_dump(args.out_dir / "reuse-manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
