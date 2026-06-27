#!/usr/bin/env python3
"""PowerShell scriptblock の動的スコープ衝突を検出する軽量監査。

PowerShell は function 内で `& $Block` を実行すると、scriptblock 側の未束縛変数が
呼び出し元ではなく wrapper function のローカル変数へ解決され得る。特に
`Invoke-LoggedCapture -CapturePath $classifyPath -Block { ... $capturePath ... }`
のように wrapper parameter と同名の変数を block 内で参照すると、外側の
`$capturePath` ではなく wrapper の `$CapturePath` を読んでしまう。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


def _mask_non_code(text: str) -> str:
    """文字列とコメントを空白化し、brace/paren の対応を取りやすくする。"""
    out = list(text)
    state = "code"
    i = 0
    while i < len(text):
        ch = text[i]
        if state == "code":
            if ch == "#":
                out[i] = " "
                state = "comment"
            elif ch == "'":
                out[i] = " "
                state = "single"
            elif ch == '"':
                out[i] = " "
                state = "double"
        elif state == "comment":
            if ch == "\n":
                state = "code"
            else:
                out[i] = " "
        elif state == "single":
            out[i] = " "
            if ch == "'" and i + 1 < len(text) and text[i + 1] == "'":
                out[i + 1] = " "
                i += 1
            elif ch == "'":
                state = "code"
        elif state == "double":
            out[i] = " "
            if ch == "`" and i + 1 < len(text):
                out[i + 1] = " "
                i += 1
            elif ch == '"':
                state = "code"
        i += 1
    return "".join(out)


def _matching_char(masked: str, start: int, open_char: str, close_char: str) -> int:
    depth = 0
    for index in range(start, len(masked)):
        ch = masked[index]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _extract_functions(text: str) -> dict[str, str]:
    masked = _mask_non_code(text)
    functions: dict[str, str] = {}
    for match in re.finditer(rf"(?im)^\s*function\s+({_IDENT}(?:-{_IDENT})*)\s*\{{", masked):
        open_brace = masked.find("{", match.end() - 1)
        close_brace = _matching_char(masked, open_brace, "{", "}")
        if close_brace < 0:
            continue
        functions[match.group(1)] = text[open_brace + 1 : close_brace]
    return functions


def _param_block(function_body: str) -> str:
    masked = _mask_non_code(function_body)
    match = re.search(r"\bparam\s*\(", masked, re.IGNORECASE)
    if not match:
        return ""
    open_paren = masked.find("(", match.end() - 1)
    close_paren = _matching_char(masked, open_paren, "(", ")")
    if close_paren < 0:
        return ""
    return function_body[open_paren + 1 : close_paren]


def _wrapper_guard_params(function_body: str) -> tuple[str, set[str]]:
    params = _param_block(function_body)
    if not params:
        return "", set()
    scriptblock_match = re.search(rf"\[scriptblock\]\s*\$({_IDENT})", params, re.IGNORECASE)
    if not scriptblock_match:
        return "", set()
    block_param = scriptblock_match.group(1)
    masked_body = _mask_non_code(function_body)
    if not re.search(rf"&\s*\${re.escape(block_param)}\b", masked_body, re.IGNORECASE):
        return "", set()
    all_params = {
        match.group(1)
        for match in re.finditer(rf"\$({_IDENT})", _mask_non_code(params), re.IGNORECASE)
    }
    scriptblock_params = {
        match.group(1).lower()
        for match in re.finditer(rf"\[scriptblock\]\s*\$({_IDENT})", params, re.IGNORECASE)
    }
    return block_param, {name for name in all_params if name.lower() not in scriptblock_params}


def _invocation_blocks(text: str, command_name: str) -> Iterable[tuple[int, int, str]]:
    masked = _mask_non_code(text)
    command_re = re.compile(rf"(?<![\w-]){re.escape(command_name)}(?![\w-])", re.IGNORECASE)
    for match in command_re.finditer(masked):
        prefix = masked[max(0, match.start() - 32) : match.start()].lower()
        if re.search(r"function\s+$", prefix):
            continue
        block_param = re.search(r"(?i)-Block\b", masked[match.end() : match.end() + 4000])
        if not block_param:
            continue
        block_param_start = match.end() + block_param.start()
        open_brace = masked.find("{", block_param_start)
        if open_brace < 0:
            continue
        close_brace = _matching_char(masked, open_brace, "{", "}")
        if close_brace < 0:
            continue
        yield match.start(), open_brace + 1, text[open_brace + 1 : close_brace]


def audit_text(text: str, *, path: str = "<string>") -> list[dict[str, object]]:
    functions = _extract_functions(text)
    wrappers: dict[str, set[str]] = {}
    for name, body in functions.items():
        _, guard_params = _wrapper_guard_params(body)
        if guard_params:
            wrappers[name] = guard_params

    findings: list[dict[str, object]] = []
    for wrapper, guard_params in wrappers.items():
        for command_start, block_start, block_text in _invocation_blocks(text, wrapper):
            for param_name in sorted(guard_params, key=str.lower):
                var_re = re.compile(rf"\$({re.escape(param_name)})(?![A-Za-z0-9_])", re.IGNORECASE)
                for var_match in var_re.finditer(block_text):
                    findings.append(
                        {
                            "path": path,
                            "line": _line_number(text, block_start + var_match.start()),
                            "wrapper": wrapper,
                            "variable": var_match.group(1),
                            "wrapper_parameter": param_name,
                            "command_line": _line_number(text, command_start),
                        }
                    )
                    break
    return findings


def audit_paths(paths: Iterable[Path | str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for raw_path in paths:
        path = Path(raw_path)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        findings.extend(audit_text(text, path=str(path)))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    args = parser.parse_args(argv)

    findings = audit_paths(args.paths)
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for item in findings:
            print(
                f"{item['path']}:{item['line']}: {item['wrapper']} block references "
                f"${item['variable']} which collides with wrapper parameter "
                f"${item['wrapper_parameter']}"
            )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
