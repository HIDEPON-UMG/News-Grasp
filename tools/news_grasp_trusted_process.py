"""News-Grasp日次本線が起動する外部processの固定信頼境界。"""

from __future__ import annotations

import ctypes
import functools
import os
import stat
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Mapping


TRUSTED_GIT = Path(r"C:\Program Files\Git\cmd\git.exe")
TRUSTED_GH = Path(r"C:\Program Files\GitHub CLI\gh.exe")
TRUSTED_GIT_SYSTEM_CONFIG = Path(r"C:\Program Files\Git\etc\gitconfig")
TRUSTED_GIT_CREDENTIAL_MANAGER = Path(
    r"C:\Program Files\Git\mingw64\bin\git-credential-manager.exe"
)
_ALLOWED_GIT_OVERRIDES = frozenset({"GIT_INDEX_FILE"})


class TrustedProcessError(RuntimeError):
    """固定実行ファイルまたはprocess環境が信頼境界を満たさない。"""


def _windows_known_folder(folder_id: str, *, reason: str) -> Path:
    if os.name != "nt":
        raise TrustedProcessError("trusted_process_windows_required")

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    guid = _Guid.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    output = ctypes.c_wchar_p()
    status = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(output)
    )
    if status != 0 or not output.value:
        raise TrustedProcessError(f"{reason}:{status}")
    try:
        return Path(output.value).resolve(strict=True)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(output)


def _reject_reparse_chain(path: Path, *, reason: str) -> None:
    cursor = Path(os.path.abspath(os.fspath(path)))
    while True:
        if cursor.exists():
            info = os.lstat(cursor)
            attributes = int(getattr(info, "st_file_attributes", 0))
            if (
                stat.S_ISLNK(info.st_mode)
                or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or getattr(cursor, "is_junction", lambda: False)()
            ):
                raise TrustedProcessError(reason)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent


def _trusted_regular_file(path: Path, *, reason: str) -> Path:
    _reject_reparse_chain(path, reason=reason)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TrustedProcessError(reason) from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise TrustedProcessError(reason)
    return resolved


@functools.lru_cache(maxsize=1)
def trusted_git_executable() -> Path:
    return _trusted_regular_file(TRUSTED_GIT, reason="trusted_git_invalid")


@functools.lru_cache(maxsize=1)
def trusted_gh_executable() -> Path:
    return _trusted_regular_file(TRUSTED_GH, reason="trusted_gh_invalid")


@functools.lru_cache(maxsize=1)
def _platform_paths() -> dict[str, Path]:
    profile = _windows_known_folder(
        "5e6c858f-0e22-4760-9afe-ea3317b67173",
        reason="trusted_profile_unavailable",
    )
    local_app_data = _windows_known_folder(
        "f1b32785-6fba-4fcf-9d55-7b8e7f157091",
        reason="trusted_local_app_data_unavailable",
    )
    roaming_app_data = _windows_known_folder(
        "3eb685db-65f9-4cf6-a03a-e3ef65729f3d",
        reason="trusted_roaming_app_data_unavailable",
    )
    windows = Path(r"C:\Windows").resolve(strict=True)
    paths = {
        "profile": profile,
        "local_app_data": local_app_data,
        "roaming_app_data": roaming_app_data,
        "temp": (local_app_data / "Temp").resolve(strict=True),
        "windows": windows,
        "system32": (windows / "System32").resolve(strict=True),
    }
    for path in paths.values():
        _reject_reparse_chain(path, reason="trusted_platform_path_reparse_forbidden")
    return paths


def daily_child_environment(
    *,
    repo_root: Path,
    python_executable: Path,
) -> dict[str, str]:
    """ambient値を継承せず、Daily childに必要な固定環境だけを生成する。"""

    paths = _platform_paths()
    python_path = _trusted_regular_file(
        python_executable, reason="trusted_python312_invalid"
    )
    git_path = trusted_git_executable()
    gh_path = trusted_gh_executable()
    git_system_config = _trusted_regular_file(
        TRUSTED_GIT_SYSTEM_CONFIG,
        reason="trusted_git_system_config_invalid",
    )
    credential_manager = _trusted_regular_file(
        TRUSTED_GIT_CREDENTIAL_MANAGER,
        reason="trusted_git_credential_manager_invalid",
    )
    media_bin = paths["profile"] / "bin"
    _reject_reparse_chain(media_bin, reason="trusted_media_bin_reparse_forbidden")
    _trusted_regular_file(media_bin / "ffmpeg.exe", reason="trusted_ffmpeg_invalid")
    _trusted_regular_file(media_bin / "ffprobe.exe", reason="trusted_ffprobe_invalid")
    path_value = os.pathsep.join(
        str(item)
        for item in (
            git_path.parent,
            credential_manager.parent,
            gh_path.parent,
            media_bin,
            python_path.parent,
            paths["system32"],
            paths["windows"],
        )
    )
    env = {
        "APPDATA": str(paths["roaming_app_data"]),
        "COMSPEC": str(paths["system32"] / "cmd.exe"),
        "GCM_INTERACTIVE": "Never",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "GH_PROMPT_DISABLED": "1",
        "GIT_AUTHOR_EMAIL": "news-grasp@localhost.invalid",
        "GIT_AUTHOR_NAME": "News-Grasp Daily",
        "GIT_COMMITTER_EMAIL": "news-grasp@localhost.invalid",
        "GIT_COMMITTER_NAME": "News-Grasp Daily",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": str(git_system_config),
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.interactive",
        "GIT_CONFIG_VALUE_0": "never",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "",
        "GIT_CONFIG_KEY_2": "credential.helper",
        "GIT_CONFIG_VALUE_2": "manager",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(paths["profile"]),
        "LOCALAPPDATA": str(paths["local_app_data"]),
        "NEWS_GRASP_REPO_ROOT": str(repo_root),
        "NoDefaultCurrentDirectoryInExePath": "1",
        "PATH": path_value,
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "SystemRoot": str(paths["windows"]),
        "TEMP": str(paths["temp"]),
        "TMP": str(paths["temp"]),
        "USERPROFILE": str(paths["profile"]),
        "WINDIR": str(paths["windows"]),
    }
    _verify_noninteractive_git_auth(env, repo_root=repo_root)
    return env


def sanitized_git_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Gitへ渡す環境をDaily childの安全なplatform値へ再縮退する。"""

    paths = _platform_paths()
    git_path = trusted_git_executable()
    git_system_config = _trusted_regular_file(
        TRUSTED_GIT_SYSTEM_CONFIG,
        reason="trusted_git_system_config_invalid",
    )
    credential_manager = _trusted_regular_file(
        TRUSTED_GIT_CREDENTIAL_MANAGER,
        reason="trusted_git_credential_manager_invalid",
    )
    env = {
        "APPDATA": str(paths["roaming_app_data"]),
        "COMSPEC": str(paths["system32"] / "cmd.exe"),
        "HOME": str(paths["profile"]),
        "LOCALAPPDATA": str(paths["local_app_data"]),
        "NoDefaultCurrentDirectoryInExePath": "1",
        "PATH": os.pathsep.join(
            (str(git_path.parent), str(credential_manager.parent), str(paths["system32"]))
        ),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "SystemRoot": str(paths["windows"]),
        "TEMP": str(paths["temp"]),
        "TMP": str(paths["temp"]),
        "USERPROFILE": str(paths["profile"]),
        "WINDIR": str(paths["windows"]),
    }
    env.update(
        {
            "GCM_INTERACTIVE": "Never",
            "GIT_AUTHOR_EMAIL": "news-grasp@localhost.invalid",
            "GIT_AUTHOR_NAME": "News-Grasp Daily",
            "GIT_COMMITTER_EMAIL": "news-grasp@localhost.invalid",
            "GIT_COMMITTER_NAME": "News-Grasp Daily",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": str(git_system_config),
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "credential.interactive",
            "GIT_CONFIG_VALUE_0": "never",
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": "",
            "GIT_CONFIG_KEY_2": "credential.helper",
            "GIT_CONFIG_VALUE_2": "manager",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    additions = {str(key): str(value) for key, value in dict(overrides or {}).items()}
    if set(additions) - _ALLOWED_GIT_OVERRIDES:
        raise TrustedProcessError("trusted_git_environment_override_forbidden")
    env.update(additions)
    return env


def _verify_noninteractive_git_auth(
    env: Mapping[str, str],
    *,
    repo_root: Path,
) -> None:
    """effective helperを固定system configのGCM一件へ限定する。"""

    git = trusted_git_executable()
    expected_config = _trusted_regular_file(
        TRUSTED_GIT_SYSTEM_CONFIG,
        reason="trusted_git_system_config_invalid",
    )
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        helpers = subprocess.run(
            [str(git), "config", "--show-origin", "--get-all", "credential.helper"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
            check=False,
            shell=False,
            env=dict(env),
            creationflags=flags,
        )
        interactive = subprocess.run(
            [str(git), "config", "--get", "credential.interactive"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
            check=False,
            shell=False,
            env=dict(env),
            creationflags=flags,
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise TrustedProcessError("trusted_git_auth_configuration_unobservable") from exc
    rows = [line.split("\t", 1) for line in helpers.stdout.splitlines() if line.strip()]
    if (
        helpers.returncode != 0
        or len(rows) != 3
        or any(len(row) != 2 for row in rows)
    ):
        raise TrustedProcessError("trusted_git_credential_helper_invalid")
    origin, helper = rows[0]
    try:
        observed_config = Path(origin.removeprefix("file:")).resolve(strict=True)
    except OSError as exc:
        raise TrustedProcessError("trusted_git_credential_helper_invalid") from exc
    if (
        os.path.normcase(str(observed_config)) != os.path.normcase(str(expected_config))
        or helper.strip() != "manager"
        or rows[1] != ["command line:", ""]
        or rows[2] != ["command line:", "manager"]
        or interactive.returncode != 0
        or interactive.stdout.strip().casefold() != "never"
    ):
        raise TrustedProcessError("trusted_git_credential_helper_invalid")


__all__ = [
    "TrustedProcessError",
    "daily_child_environment",
    "sanitized_git_environment",
    "trusted_gh_executable",
    "trusted_git_executable",
]
