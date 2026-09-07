"""DeepDiveの実入力と独立reviewの採点結果を束縛する。"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from tools.deepdive_quality import DEEPDIVE_QUALITY_REVIEW_AXES

MAX_EVIDENCE_BYTES = 4 * 1024 * 1024


def evidence_paths(issue_date: str) -> dict[str, str]:
    from datetime import date
    date.fromisoformat(issue_date)
    return {'article': f'digest/DeepDive/{issue_date}-DeepDive.md',
            'dialogue': f'digest/DeepDive/{issue_date}-DeepDive-dialogue.md',
            'provenance': f'data/deepdive-provenance/{issue_date}.json',
            'review': f'data/deepdive-quality-review/{issue_date}.json'}


def stable_bytes(root: Path, relative: str) -> bytes:
    from tools import news_grasp_daily_content as content
    from tools.news_grasp_deterministic_builders import _safe_regular_bytes, NewsGraspBuilderError
    raw_path = root / relative
    if content._has_reparse_ancestor(raw_path):
        raise ValueError('review_input_reparse')
    path = content._safe_path(root, relative)
    try:
        raw = _safe_regular_bytes(path, maximum=MAX_EVIDENCE_BYTES, code='review_input_unstable')
    except NewsGraspBuilderError as exc:
        raise ValueError('review_input_unstable') from exc
    if content._has_reparse_ancestor(raw_path):
        raise ValueError('review_input_reparse')
    return raw


def read_inputs(root: Path, issue_date: str) -> dict[str, Any]:
    """同一bounded bytesからreview入力と全artifact identityを作る。"""
    from tools import deepdive_quality as quality
    paths = evidence_paths(issue_date)
    article = stable_bytes(root, paths['article'])
    dialogue = stable_bytes(root, paths['dialogue'])
    provenance = stable_bytes(root, paths['provenance'])
    failures, observations = quality._validate_provenance_with_evidence(
        root / paths['article'], root / paths['provenance'],
        article_bytes_override=article, manifest_bytes_override=provenance)
    expected = {str((root / paths['article']).resolve()): digest(article),
                str((root / paths['provenance']).resolve()): digest(provenance)}
    if failures or {row['path']: row['sha256'] for row in observations} != expected:
        raise ValueError('review_provenance_invalid:' + ';'.join(failures))
    relation = quality._relation_review_payload(root / paths['article'],
                                                article_bytes_override=article)
    return {'issueDate': issue_date,
            'artifacts': {
                'article': {'path': paths['article'], 'sha256': digest(article)},
                'relation': {'path': paths['article'], 'sha256': digest(canonical_bytes(relation))},
                'dialogue': {'path': paths['dialogue'], 'sha256': digest(dialogue)}},
            'articleMarkdown': article.decode('utf-8-sig'),
            'dialogueMarkdown': dialogue.decode('utf-8-sig'),
            'provenance': json.loads(provenance.decode('utf-8-sig')),
            'provenanceSha256': digest(provenance)}


def prepare_provenance(root: Path, issue_date: str) -> dict[str, bytes]:
    """既存有効証拠を再使用し、不足時だけ実取得して未公開のbytesを返す。"""
    from tools import deepdive_quality as quality
    paths = evidence_paths(issue_date)
    article_before = stable_bytes(root, paths['article'])
    if (root / paths['provenance']).exists():
        try:
            read_inputs(root, issue_date)
            return {}
        except (ValueError, OSError):
            pass
    with tempfile.TemporaryDirectory(prefix='news-grasp-provenance-stage-') as temporary:
        output = Path(temporary) / 'provenance.json'
        quality.capture_provenance(article_path=root / paths['article'], output_path=output,
                                   article_bytes_override=article_before)
        value = stable_bytes(Path(temporary), output.name)
    if stable_bytes(root, paths['article']) != article_before:
        raise ValueError('review_input_changed_during_capture')
    return {paths['provenance']: value}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def review_binding(inputs: Mapping[str, Any], review: Mapping[str, Any], *,
                   call_id: str, raw_bytes: bytes) -> dict[str, Any]:
    """この束縛はcanonical call/checkpointとの照合を伴う場合だけ採用できる。"""
    return {'schemaVersion': 'NEWS_GRASP_DAILY_REVIEW_BINDING_V1',
            'callId': call_id, 'inputHash': digest(canonical_bytes(inputs)),
            'rawSha256': digest(raw_bytes), 'resultSha256': digest(canonical_bytes(review)),
            'reviewerRole': 'deepdive_review',
            'reviewRoute': 'production_generation'}


def validate_review_binding(inputs: Mapping[str, Any], review: Mapping[str, Any],
                            receipt: Mapping[str, Any], *, raw_bytes: bytes) -> bool:
    try:
        expected = review_binding(inputs, review, call_id=receipt['callId'], raw_bytes=raw_bytes)
        rebound = bind_review_output(inputs, json.loads(raw_bytes.decode('utf-8')))
        if dict(receipt) != expected or dict(review) != rebound or not receipt['callId']:
            raise ValueError('mismatch')
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise ValueError('review_binding_invalid') from exc
    return True


def bind_review_output(inputs: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    """モデルにhash・status・平均を決めさせず、採点と具体的所見だけを受理する。"""
    axes = set(DEEPDIVE_QUALITY_REVIEW_AXES)
    if not isinstance(raw, Mapping) or set(raw) != {'scores', 'findings'}:
        raise ValueError('review_output_shape')
    scores, findings = raw['scores'], raw['findings']
    if not isinstance(scores, Mapping) or set(scores) != axes:
        raise ValueError('review_score_invalid')
    if any(type(value) is not int or not 1 <= value <= 5 for value in scores.values()):
        raise ValueError('review_score_invalid')
    if not isinstance(findings, Mapping) or set(findings) != axes:
        raise ValueError('review_finding_invalid')
    if any(not isinstance(value, str) or not value.strip() or len(value) > 8000
           for value in findings.values()):
        raise ValueError('review_finding_invalid')
    average = sum(scores.values()) / len(axes)
    return {
        'schemaVersion': 'DEEPDIVE_QUALITY_REVIEW_V2',
        'issueDate': inputs['issueDate'],
        'artifacts': copy.deepcopy(inputs['artifacts']),
        'scores': dict(scores),
        'findings': dict(findings),
        'averageScore': average,
        'reviewRoute': 'production_generation',
        'status': 'Green' if average >= 4 and min(scores.values()) >= 3 else 'Red',
    }


def review_repair_fields(review: Mapping[str, Any]) -> list[str]:
    """失敗した意味品質軸が作用する本文だけを修復へ戻す。"""
    scores = review.get('scores') or {}
    failed = {axis for axis, value in scores.items() if type(value) is int and value < 4}
    fields = set()
    if 'dialogue_naturalness' in failed:
        fields.add('dialogue_markdown')
    if 'relation_map_utility' in failed:
        fields.add('article_markdown')
    if failed - {'dialogue_naturalness', 'relation_map_utility'}:
        fields.update(('article_markdown', 'dialogue_markdown'))
    return sorted(fields)


def ensure_evidence(*, root: Path, issue_date: str, run_id: str, ledger: Any,
                    consume_model_call, model_fn, codex_executable_provider) -> tuple[dict[str, Any], bool]:
    """保存rawを再利用し、実入力に対応するreviewだけをmediaの前へ渡す。"""
    from contextlib import closing
    from tools import news_grasp_daily_content as content
    from tools import news_grasp_direct_runtime as runtime
    from tools import deepdive_quality as quality

    artifact_id = 'deepdive_evidence'
    validator_id = 'deepdive_evidence_v1'
    paths = evidence_paths(issue_date)
    ledger.assert_writer()

    def model_failure(fields: list[str], detail: str) -> dict[str, Any]:
        previous = ledger.list_checkpoints().get('deepdive_model') or {}
        if not previous.get('payload') or not fields:
            raise content.ModelResultPending('deepdive_evidence_repair_scope_unknown')
        return content._failure_checkpoint_value(run_id=run_id, issue_date=issue_date,
            stage='deepdive_evidence', artifact_id='deepdive_model',
            predicate_id='deepdive_value',
            reason_code='DEEPDIVE_OUTPUT_INVALID:review:' + ','.join(fields) + '|' + detail,
            input_hash=previous['inputHash'], cause_input_mask=('deepdive_model',),
            invalid_payload=previous['payload'])

    try:
        provenance_outputs = prepare_provenance(root, issue_date)
        if provenance_outputs:
            with ledger.materialization_fence():
                content._atomic_apply(root, provenance_outputs)
        inputs = read_inputs(root, issue_date)
    except (OSError, ValueError, quality.DeepDiveQualityError) as exc:
        detail = str(exc)
        if any(part in detail for part in ('Timeout', 'URLError', 'CERTIFICATE', 'HTTP Error 403', 'HTTP Error 5', 'SYSTEM_FETCH_FAILED')):
            raise content.ModelResultPending('deepdive_provenance_observation_pending') from exc
        if isinstance(exc, OSError) or any(part in detail for part in ('reparse', 'unstable', 'changed_during', 'UNSAFE_INPUT')):
            raise content.ModelResultPending('deepdive_evidence_input_integrity') from exc
        fields = ['article_markdown'] if 'relations block' in detail else ['article_markdown', 'dialogue_markdown']
        ledger.record_failure(model_failure(fields, detail))
        raise content.DailyContentError('DEEPDIVE_EVIDENCE_SOURCE_RED:' + detail) from exc

    input_hash = digest(canonical_bytes(inputs))
    call_id = digest(f'repair|{artifact_id}|{input_hash}'.encode())
    call_root = content._model_call_root(root, run_id, call_id)

    def canonical_call() -> dict[str, Any] | None:
        with closing(ledger.store.connect()) as db:
            row = db.execute('SELECT artifact_id,input_hash,status,output_hash FROM daily_model_calls WHERE run_id=? AND call_id=?',
                             (run_id, call_id)).fetchone()
        return dict(row) if row is not None else None

    def expected_call_hash(payload: Mapping[str, Any]) -> str:
        output_hash = digest(runtime._json_dump(dict(payload)).encode('utf-8'))
        return digest(runtime._json_dump({'green':{artifact_id:output_hash}, 'red':{}}).encode('utf-8'))

    def saved_raw_bytes() -> bytes:
        expected_intent = content._model_call_intent(root=root, run_id=run_id, issue_date=issue_date,
            role='deepdive_review', category=None, call_id=call_id, input_hash=input_hash)
        intent = call_root / 'intent.json'
        if not intent.is_file() or content._has_reparse_ancestor(intent):
            raise content.ModelResultPending('deepdive_review_intent_missing')
        content._ensure_model_call_intent(intent, expected_intent)
        raw_path = call_root / content.MODEL_CALL_RAW_FILENAME
        if not raw_path.is_file():
            recovery = call_root / 'schema-recovery'
            if not recovery.is_dir() or content._has_reparse_ancestor(recovery):
                raise content.ModelResultPending('deepdive_review_raw_missing')
            events_sha = content._confirmed_schema_rejection_sha256(call_root / 'deepdive_review.events.jsonl')
            schema_sha = content._verified_model_schema_sha256(root, 'deepdive_review', pending_detail='deepdive_review:schema')
            if (not events_sha or not content._schema_recovery_metadata_matches(recovery / 'metadata.json',
                    call_id=call_id, original_events_sha=events_sha, schema_sha=schema_sha)):
                raise content.ModelResultPending('deepdive_review_recovery_unconfirmed')
            recovery_intent = recovery / 'intent.json'
            if not recovery_intent.is_file():
                raise content.ModelResultPending('deepdive_review_recovery_unconfirmed')
            content._ensure_model_call_intent(recovery_intent, expected_intent)
            raw_path = recovery / content.MODEL_CALL_RAW_FILENAME
        return stable_bytes(root, raw_path.relative_to(root).as_posix())

    def materialize(payload: Mapping[str, Any], raw_bytes: bytes) -> None:
        if set(payload) != {'review', 'binding', 'artifactHashes'}:
            raise content.ModelResultPending('deepdive_review_checkpoint_invalid')
        validate_review_binding(inputs, payload['review'], payload['binding'], raw_bytes=raw_bytes)
        expected_hashes = {paths['provenance']: inputs['provenanceSha256'],
                           paths['review']: digest(content._json_bytes(payload['review']))}
        if payload['artifactHashes'] != expected_hashes or payload['review']['status'] != 'Green':
            raise content.ModelResultPending('deepdive_review_checkpoint_invalid')
        if digest(canonical_bytes(read_inputs(root, issue_date))) != input_hash:
            raise content.ModelResultPending('deepdive_review_input_changed')
        with ledger.materialization_fence():
            content._atomic_apply(root, {paths['review']: content._json_bytes(payload['review'])})

    checkpoint = ledger.load_checkpoint(artifact_id=artifact_id, input_hash=input_hash, validator_id=validator_id)
    if checkpoint is not None:
        recorded = canonical_call()
        payload = checkpoint['payload']
        if (not recorded or recorded['status'] != 'completed' or recorded['artifact_id'] != artifact_id
                or recorded['input_hash'] != input_hash or recorded['output_hash'] != expected_call_hash(payload)):
            raise content.ModelResultPending('deepdive_review_call_unconfirmed')
        try:
            raw_bytes = saved_raw_bytes()
            materialize(payload, raw_bytes)
        except (OSError, ValueError) as exc:
            raise content.ModelResultPending('deepdive_review_saved_binding_invalid') from exc
        return checkpoint, False

    reservation = consume_model_call(call_id=call_id, budget_class='repair', artifact_id=artifact_id, input_hash=input_hash)
    if reservation.get('status') in {'completed', 'completed_partial'}:
        raw = json.loads(saved_raw_bytes().decode('utf-8'))
        sent = False
    else:
        with content._persistent_model_output_context(root, run_id):
            raw, sent = content._invoke_persistent_model_call(root=root, run_id=run_id, issue_date=issue_date,
                reservation=reservation, role='deepdive_review', category=None, call_id=call_id,
                input_hash=input_hash, artifact_id=artifact_id, model_fn=model_fn,
                codex_executable_provider=codex_executable_provider, review_inputs=inputs)
    try:
        review = bind_review_output(inputs, raw)
        raw_bytes = saved_raw_bytes()
        binding = review_binding(inputs, review, call_id=call_id, raw_bytes=raw_bytes)
    except (OSError, ValueError) as exc:
        raise content.ModelResultPending('deepdive_review_output_invalid') from exc
    if digest(canonical_bytes(read_inputs(root, issue_date))) != input_hash:
        raise content.ModelResultPending('deepdive_review_input_changed')
    payload = {'review':review, 'binding':binding,
               'artifactHashes':{paths['provenance']:inputs['provenanceSha256'],
                                 paths['review']:digest(content._json_bytes(review))}}
    with ledger.materialization_fence():
        content._atomic_apply(root, {paths['review']:content._json_bytes(review)})

    failure_fields = review_repair_fields(review) if review['status'] != 'Green' else []
    details = json.dumps({'scores':review['scores'], 'findings':review['findings']}, ensure_ascii=False)
    if not ledger.store.test_only_allow_semantic_verifier and not failure_fields:
        audit = quality.audit_issue(repo_root=root, issue_date=issue_date, include_corpus=False,
                                    require_rendered_public=False, route='production_generation')
        if audit.get('status') != 'Green':
            if any('UNSAFE_INPUT' in str(issue) for issue in audit.get('issues') or []):
                raise content.ModelResultPending('deepdive_evidence_input_integrity')
            codes = set(audit.get('issueCodes') or [])
            failure_fields = (['dialogue_markdown'] if codes == {'deepdive_dialogue_value_invalid'}
                              else ['article_markdown', 'dialogue_markdown'])
            details = json.dumps(audit.get('issues') or ['shared_quality_red'], ensure_ascii=False)
    if failure_fields:
        failure = model_failure(failure_fields, details)
        evidence_failure = content._failure_checkpoint_value(run_id=run_id, issue_date=issue_date,
            stage='deepdive_evidence', artifact_id=artifact_id, predicate_id='deepdive_value',
            reason_code='DEEPDIVE_REVIEW_RED', input_hash=input_hash,
            cause_input_mask=('deepdive_model',), invalid_payload=payload)
        if reservation.get('status') == 'reserved':
            ledger.commit_model_call_partial(call_id=call_id, artifacts={},
                failures={'deepdive_model':failure, artifact_id:evidence_failure})
        else:
            ledger.record_failure(failure)
            ledger.record_failure(evidence_failure)
        raise content.DailyContentError('DEEPDIVE_REVIEW_RED')

    if reservation.get('status') == 'reserved':
        checkpoint = ledger.commit_model_call(call_id=call_id, artifacts={artifact_id:{
            'inputHash':input_hash, 'validatorId':validator_id, 'payload':payload}})[artifact_id]
    else:
        recorded = canonical_call()
        if not recorded or recorded['status'] != 'completed' or recorded['output_hash'] != expected_call_hash(payload):
            raise content.ModelResultPending('deepdive_review_call_unconfirmed')
        checkpoint = ledger.write_checkpoint(artifact_id=artifact_id, input_hash=input_hash,
                                              validator_id=validator_id, payload=payload)
    return checkpoint, sent
