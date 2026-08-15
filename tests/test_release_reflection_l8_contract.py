from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"


def test_l8_consumes_the_single_release_reflection_receipt_before_admission() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    required = [
        "[Parameter(Mandatory=$true)][string] $ReleaseReflectionReceiptPath",
        "tools\\harness\\release_reflection_receipt.py",
        "'validate' '--receipt' $ReleaseReflectionReceiptPath",
        "NEWS_GRASP_RELEASE_REFLECTION_INVALID",
        "NEWS_GRASP_RELEASE_REFLECTION_RUNTIME_REF_MISMATCH",
        "[string]$releaseReflection.l8Mode -cne 'consume-only'",
        "[int]$releaseReflection.producerInvocationCount -ne 1",
        "[string]$releaseReflection.sourceCommit -cne $executionRepoCommit",
        "[string]$releaseReflection.remoteHead -cne $runtimeRepoCommit",
        "releaseReflectionReceiptPath = [System.IO.Path]::GetFullPath($ReleaseReflectionReceiptPath)",
        "release_reflection_receipt_sha256 = $releaseReflectionReceiptSha256",
    ]
    for fragment in required:
        assert fragment in text, fragment
    receipt_validation = text.index("'validate' '--receipt' $ReleaseReflectionReceiptPath")
    admission = text.index("$e2eAdmissionValidation =")
    launch = text.index("& $installedTaskPythonPath @installedLauncherArguments")
    assert receipt_validation < admission < launch


def test_l8_does_not_reissue_or_write_a_second_release_reflection_receipt() -> None:
    text = WRAPPER.read_text(encoding="utf-8-sig")
    assert "create_release_reflection_receipt" not in text
    assert "producerInvocationCount = 2" not in text
    assert "l8Mode = 'produce'" not in text
