"""authorize後のproof差替えをconsumeの実bytes境界で拒否する。"""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools import e2e_final_admission_bridge as bridge


def _fixture(tmp_path: Path):
    registry = {"schemaVersion": "RECOVERY_PATTERN_REGISTRY_V2", "patterns": []}
    proof = {
        "schemaVersion": "HIGH_COST_RECOVERY_PROOF_V2",
        "registrySha256": bridge._canonical_sha256(registry),
        "successor": {"admissionId": "successor"},
    }
    proof["proofSha256"] = bridge._canonical_sha256(proof)
    path = tmp_path / "proof.json"
    raw = json.dumps(proof).encode()
    path.write_bytes(raw)
    parent = {
        "authorizationMode": "causal_replacement",
        "causalReplacementProofPath": str(path),
        "causalReplacementProofFileSha256": hashlib.sha256(raw).hexdigest(),
        "causalReplacementProofSha256": proof["proofSha256"],
    }
    return path, proof, parent, registry


@pytest.mark.parametrize("mutation", ["none", "bytes", "seal", "registry", "path"])
def test_consume_rebinds_authorized_bytes_seal_registry_and_locator(tmp_path, mutation):
    path, proof, parent, registry = _fixture(tmp_path)
    if mutation == "bytes":
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "seal":
        proof["successor"]["admissionId"] = "replacement"
        raw = json.dumps(proof).encode()
        path.write_bytes(raw)
        parent["causalReplacementProofFileSha256"] = hashlib.sha256(raw).hexdigest()
    elif mutation == "registry":
        registry = copy.deepcopy(registry)
        registry["patterns"].append({"patternId": "changed"})
    elif mutation == "path":
        parent["causalReplacementProofPath"] = str(tmp_path / "another.json")
    raw = path.read_bytes()
    call = lambda: bridge._validate_causal_replacement_consume_binding(
        proof=proof,
        proof_path=path,
        proof_file_sha256=hashlib.sha256(raw).hexdigest(),
        parent=parent,
        registry=registry,
    )
    if mutation == "none":
        call()
    else:
        with pytest.raises(bridge.E2EFinalAdmissionError, match="E2E_CAUSAL_REPLACEMENT_BINDING_DRIFT"):
            call()
