from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tools.benchmark_code_safety import (
    benchmark_subprocess_env,
    run_limited_benchmark_process,
    validate_benchmark_python,
)


def test_safe_generated_python_is_accepted() -> None:
    validate_benchmark_python("def solve(values):\n    return sorted(values)\n")


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.remove('x')\n",
        "open('x', 'w').write('bad')\n",
        "eval('1 + 1')\n",
        "value = ().__class__.__base__.__subclasses__()\n",
        "while True:\n    print('x')\n",
    ],
)
def test_unsafe_generated_python_is_rejected(source: str) -> None:
    with pytest.raises(ValueError, match="unsafe benchmark code"):
        validate_benchmark_python(source)


def test_benchmark_subprocess_env_does_not_inherit_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("NEWS_GRASP_PRIVATE_TOKEN", "must-not-leak")

    env = benchmark_subprocess_env(tmp_path)

    assert "OPENAI_API_KEY" not in env
    assert "NEWS_GRASP_PRIVATE_TOKEN" not in env
    assert env["HOME"] == str(tmp_path)
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    if os.name == "nt":
        assert "SystemRoot" in env


def test_benchmark_process_enforces_output_byte_limit(tmp_path: Path) -> None:
    result = run_limited_benchmark_process(
        [sys.executable, "-c", "print('x' * 200000)"],
        cwd=tmp_path,
        env=benchmark_subprocess_env(tmp_path),
        timeout_sec=10,
        max_output_bytes=4096,
        max_working_set_bytes=256 * 1024 * 1024,
    )

    assert result.returncode == 125
    assert "output limit exceeded" in result.stderr
    assert len(result.stdout.encode("utf-8")) <= 4096


@pytest.mark.skipif(os.name != "nt", reason="Windows working-set contract")
def test_benchmark_process_enforces_working_set_limit(tmp_path: Path) -> None:
    result = run_limited_benchmark_process(
        [
            sys.executable,
            "-c",
            "import time; data=bytearray(64*1024*1024); data[::4096]=b'x'*(len(data)//4096); time.sleep(5)",
        ],
        cwd=tmp_path,
        env=benchmark_subprocess_env(tmp_path),
        timeout_sec=10,
        max_output_bytes=4096,
        max_working_set_bytes=16 * 1024 * 1024,
    )

    assert result.returncode == 125
    assert "working set limit exceeded" in result.stderr
