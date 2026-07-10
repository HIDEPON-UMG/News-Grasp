from tools.judge_deepdive_triad import MODELS, ORDERS, QUALITY_WEIGHTS, weighted_quality_score


def test_triad_judge_rotates_all_models_through_all_positions() -> None:
    assert len(MODELS) == 3
    assert len(ORDERS) == 3
    for position in range(3):
        assert {order[position] for order in ORDERS} == set(MODELS)


def test_deepdive_weights_prioritize_decision_support() -> None:
    assert QUALITY_WEIGHTS == {
        "readability": 0.10,
        "coherence": 0.15,
        "natural_japanese": 0.10,
        "information_density": 0.15,
        "insight": 0.25,
        "non_repetition": 0.05,
        "reader_usefulness": 0.20,
    }
    assert sum(QUALITY_WEIGHTS.values()) == 1.0
    assert weighted_quality_score({key: 5 for key in QUALITY_WEIGHTS}) == 5.0
