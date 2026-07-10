from tools.run_model_benchmark import (
    API_PRICES_PER_MILLION,
    CASES,
    estimate_api_cost_usd,
    parse_usage_jsonl,
)


def test_role_matched_benchmark_matrix_uses_five_repetitions() -> None:
    assert set(CASES) == {"reporter", "style_editor", "newsroom_editor", "deepdive"}
    assert CASES["reporter"]["models"] == ["gpt-5.4", "gpt-5.6-terra"]
    assert CASES["style_editor"]["models"] == ["gpt-5.4-mini", "gpt-5.6-luna"]
    assert CASES["newsroom_editor"]["models"] == ["gpt-5.4", "gpt-5.6-terra"]
    assert CASES["deepdive"]["models"] == ["gpt-5.5", "gpt-5.6-sol"]


def test_official_api_prices_are_recorded_per_million_tokens() -> None:
    assert API_PRICES_PER_MILLION["gpt-5.6-sol"] == {"input": 5.0, "cached_input": 0.5, "output": 30.0}
    assert API_PRICES_PER_MILLION["gpt-5.6-terra"] == {"input": 2.5, "cached_input": 0.25, "output": 15.0}
    assert API_PRICES_PER_MILLION["gpt-5.6-luna"] == {"input": 1.0, "cached_input": 0.1, "output": 6.0}


def test_parse_usage_and_estimate_cost_separates_cached_input() -> None:
    text = "\n".join([
        '{"type":"thread.started","thread_id":"t1"}',
        '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":200,"reasoning_output_tokens":50}}',
    ])
    usage = parse_usage_jsonl(text)
    assert usage == {
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "output_tokens": 200,
        "reasoning_output_tokens": 50,
    }
    cost = estimate_api_cost_usd("gpt-5.6-luna", usage)
    assert cost == (600 * 1.0 + 400 * 0.1 + 200 * 6.0) / 1_000_000
