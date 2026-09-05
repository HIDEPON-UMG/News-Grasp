from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"


def test_l8_does_not_depend_on_post_promotion_release_reflection() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    forbidden = [
        "ReleaseReflectionReceiptPath",
        "tools\\harness\\release_reflection_receipt.py",
        "NEWS_GRASP_RELEASE_REFLECTION_RUNTIME_REF_MISMATCH",
        "$runtimeRepoCommit",
    ]
    for fragment in forbidden:
        assert fragment not in text, fragment
    candidate_validation = text.index("NEWS_GRASP_NOPUBLISH_CANDIDATE_IDENTITY_INVALID")
    admission = text.index("$e2eAdmissionValidation =")
    launch = text.index("'-B' $nopublishOwnerPath")
    assert candidate_validation < admission < launch


def test_l8_does_not_reissue_or_write_a_second_release_reflection_receipt() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "create_release_reflection_receipt" not in text
    assert "producerInvocationCount = 2" not in text
    assert "l8Mode = 'produce'" not in text
