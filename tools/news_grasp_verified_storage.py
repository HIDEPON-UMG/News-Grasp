"""News-Grasp managed JSON persistence with pinned directory boundaries.

Windows production is the authority surface.  Directory handles are opened without
``FILE_SHARE_DELETE`` for the whole critical section so an ancestor cannot be
renamed or replaced by a junction between validation and use.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterator, Mapping


REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_lexical(left))) == os.path.normcase(
        os.fspath(_lexical(right))
    )


def _is_reparse(path: Path) -> bool:
    try:
        item = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(item.st_mode) or bool(
        int(getattr(item, "st_file_attributes", 0)) & REPARSE_FLAG
    )


def _contained(path: Path, root: Path, *, code: str) -> tuple[Path, Path]:
    candidate = _lexical(path)
    boundary = _lexical(root)
    if candidate == boundary or boundary not in candidate.parents:
        raise ValueError(code)
    return candidate, boundary


def validated_managed_root(
    *,
    repo_root: Path,
    relative_parts: tuple[str, ...],
    create: bool,
    code: str,
) -> Path:
    """Create/validate an exact repo-local directory chain without reparse nodes."""

    repo = _lexical(repo_root)
    if not repo.is_dir() or _is_reparse(repo):
        raise ValueError(code)
    current = repo
    for part in relative_parts:
        if not part or part in {".", ".."} or Path(part).name != part:
            raise ValueError(code)
        current = current / part
        if current.exists():
            if not current.is_dir() or _is_reparse(current):
                raise ValueError(code)
        elif create:
            try:
                current.mkdir(exist_ok=True)
            except OSError as error:
                raise ValueError(code) from error
            if not current.is_dir() or _is_reparse(current):
                raise ValueError(code)
        else:
            return current
    try:
        resolved_repo = repo.resolve(strict=True)
        resolved_current = current.resolve(strict=True)
    except OSError as error:
        raise ValueError(code) from error
    if resolved_repo not in resolved_current.parents or not _same_path(
        resolved_current, current
    ):
        raise ValueError(code)
    return current


def _components(path: Path, anchor: Path, *, code: str) -> list[Path]:
    root = _lexical(path)
    trusted = _lexical(anchor)
    try:
        relative = root.relative_to(trusted)
    except ValueError as error:
        raise ValueError(code) from error
    values = [trusted]
    current = trusted
    for part in relative.parts:
        current = current / part
        values.append(current)
    if any(not item.is_dir() or _is_reparse(item) for item in values):
        raise ValueError(code)
    return values


@contextmanager
def pinned_directory(path: Path, *, anchor: Path, code: str) -> Iterator[None]:
    """Pin every directory component for the duration of a managed operation."""

    components = _components(path, anchor, code=code)
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptors: list[int] = []
        identities: list[tuple[int, int]] = []
        try:
            for component in components:
                descriptor = os.open(component, flags)
                metadata = os.fstat(descriptor)
                descriptors.append(descriptor)
                identities.append((metadata.st_dev, metadata.st_ino))
            yield
            for component, identity in zip(components, identities, strict=True):
                current = os.lstat(component)
                if (current.st_dev, current.st_ino) != identity or _is_reparse(
                    component
                ):
                    raise ValueError(code)
        except OSError as error:
            raise ValueError(code) from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        return

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handles: list[int] = []
    try:
        for component in components:
            directory_handle = kernel32.CreateFileW(
                str(component),
                0x0080,  # FILE_READ_ATTRIBUTES
                0x0001 | 0x0002,  # share read/write, deliberately not delete
                None,
                3,  # OPEN_EXISTING
                0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
                None,
            )
            if directory_handle in (0, ctypes.c_void_p(-1).value):
                raise ValueError(code)
            handles.append(directory_handle)
            pin_path = component / ".managed-root.pin"
            pin_handle = kernel32.CreateFileW(
                str(pin_path),
                0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
                0x0001 | 0x0002,  # share read/write, deliberately not delete
                None,
                4,  # OPEN_ALWAYS
                0x00000002 | 0x00200000,  # HIDDEN | OPEN_REPARSE_POINT
                None,
            )
            if pin_handle in (0, ctypes.c_void_p(-1).value):
                raise ValueError(code)
            handles.append(pin_handle)
            if _is_reparse(component):
                raise ValueError(code)
            if _is_reparse(pin_path):
                raise ValueError(code)
        yield
        if any(
            _is_reparse(component) or _is_reparse(component / ".managed-root.pin")
            for component in components
        ):
            raise ValueError(code)
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


def read_bytes(
    path: Path,
    *,
    root: Path,
    max_bytes: int,
    code: str,
) -> bytes:
    candidate, boundary = _contained(path, root, code=code)
    with pinned_directory(candidate.parent, anchor=boundary, code=code):
        try:
            before = os.lstat(candidate)
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or int(getattr(before, "st_file_attributes", 0)) & REPARSE_FLAG
                or int(getattr(before, "st_nlink", 1)) != 1
                or before.st_size > max_bytes
            ):
                raise ValueError(code)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            descriptor = os.open(candidate, flags)
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError(code)
                chunks: list[bytes] = []
                remaining = max_bytes + 1
                while remaining > 0:
                    chunk = os.read(descriptor, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                after_handle = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            after_path = os.lstat(candidate)
        except (OSError, ValueError) as error:
            raise ValueError(code) from error
        if (
            len(raw) > max_bytes
            or len(raw) != before.st_size
            or (after_handle.st_dev, after_handle.st_ino, after_handle.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or (after_path.st_dev, after_path.st_ino, after_path.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or after_path.st_mtime_ns != before.st_mtime_ns
        ):
            raise ValueError(code)
        return raw


def read_json(
    path: Path,
    *,
    root: Path,
    max_bytes: int,
    code: str,
) -> dict[str, Any]:
    try:
        value = json.loads(
            read_bytes(path, root=root, max_bytes=max_bytes, code=code).decode(
                "utf-8-sig"
            )
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(code) from error
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def atomic_write_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    root: Path,
    code: str,
) -> str:
    candidate, boundary = _contained(path, root, code=code)
    if not candidate.parent.is_dir():
        raise ValueError(code)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    temporary: Path | None = None
    with pinned_directory(candidate.parent, anchor=boundary, code=code):
        if candidate.exists():
            item = os.lstat(candidate)
            if (
                not stat.S_ISREG(item.st_mode)
                or stat.S_ISLNK(item.st_mode)
                or int(getattr(item, "st_file_attributes", 0)) & REPARSE_FLAG
                or int(getattr(item, "st_nlink", 1)) != 1
            ):
                raise ValueError(code)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{candidate.name}.", suffix=".tmp", dir=candidate.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temp_item = os.lstat(temporary)
            if (
                not stat.S_ISREG(temp_item.st_mode)
                or _is_reparse(temporary)
                or int(getattr(temp_item, "st_nlink", 1)) != 1
            ):
                raise ValueError(code)
            os.replace(temporary, candidate)
            temporary = None
            installed = os.lstat(candidate)
            if (
                (installed.st_dev, installed.st_ino)
                != (temp_item.st_dev, temp_item.st_ino)
                or _is_reparse(candidate)
                or int(getattr(installed, "st_nlink", 1)) != 1
            ):
                raise ValueError(code)
        except (OSError, ValueError) as error:
            raise ValueError(code) from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    installed_raw = read_bytes(
        candidate, root=boundary, max_bytes=max(len(payload), 1), code=code
    )
    if installed_raw != payload:
        raise ValueError(code)
    return hashlib.sha256(installed_raw).hexdigest()


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    root: Path,
    max_bytes: int,
    code: str,
) -> str:
    """Write bounded bytes under a pinned managed directory and verify the result."""

    if len(payload) > max_bytes:
        raise ValueError(code)
    candidate, boundary = _contained(path, root, code=code)
    if not candidate.parent.is_dir():
        raise ValueError(code)
    temporary: Path | None = None
    with pinned_directory(candidate.parent, anchor=boundary, code=code):
        if candidate.exists():
            item = os.lstat(candidate)
            if (
                not stat.S_ISREG(item.st_mode)
                or stat.S_ISLNK(item.st_mode)
                or int(getattr(item, "st_file_attributes", 0)) & REPARSE_FLAG
                or int(getattr(item, "st_nlink", 1)) != 1
            ):
                raise ValueError(code)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{candidate.name}.", suffix=".tmp", dir=candidate.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temp_item = os.lstat(temporary)
            if (
                not stat.S_ISREG(temp_item.st_mode)
                or _is_reparse(temporary)
                or int(getattr(temp_item, "st_nlink", 1)) != 1
            ):
                raise ValueError(code)
            os.replace(temporary, candidate)
            temporary = None
            installed = os.lstat(candidate)
            if (
                (installed.st_dev, installed.st_ino)
                != (temp_item.st_dev, temp_item.st_ino)
                or _is_reparse(candidate)
                or int(getattr(installed, "st_nlink", 1)) != 1
            ):
                raise ValueError(code)
        except (OSError, ValueError) as error:
            raise ValueError(code) from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    installed_raw = read_bytes(
        candidate, root=boundary, max_bytes=max_bytes, code=code
    )
    if installed_raw != payload:
        raise ValueError(code)
    return hashlib.sha256(installed_raw).hexdigest()
