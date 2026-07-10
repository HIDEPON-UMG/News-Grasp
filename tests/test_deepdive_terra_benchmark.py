from tools.run_deepdive_terra_benchmark import JUDGE_MODEL, PAIRS, TERRA_MODEL, strip_scores


def test_deepdive_terra_retest_compares_current_and_quality_candidate() -> None:
    assert TERRA_MODEL == "gpt-5.6-terra"
    assert PAIRS == (("gpt-5.5", TERRA_MODEL), (TERRA_MODEL, "gpt-5.6-sol"))
    assert JUDGE_MODEL not in {model for pair in PAIRS for model in pair}


def test_blind_bundle_removes_model_identity_and_self_scores() -> None:
    source = {"model": "hidden", "self_score": {"quality": 5}, "article": {"title": "x", "self_score": {"quality": 4}}}
    assert strip_scores(source) == {"article": {"title": "x"}}
