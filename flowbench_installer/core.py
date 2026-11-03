"""Platform-independent install, update and uninstall steps.

The payload is a ``tar.xz`` of the PyInstaller ``dist/FlowBench`` folder.
Installing never writes into the live folder: files are extracted to a
sibling staging folder, which is then swapped in with two renames.  A failed
or cancelled install therefore leaves the previous version untouched, and a
running FlowBench (whose files Windows keeps locked) is detected by the swap
instead of leaving a half-updated folder behind.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

APP_NAME = "FlowBench"
APP_EXE = "FlowBench.exe"
UNINSTALL_EXE = "FlowBench-Uninstall.exe"
MARKER_NAME = "FlowBench.install.json"
PAYLOAD_NAME = "FlowBench-payload.tar.xz"
PAYLOAD_INFO_NAME = "payload.json"
CHUNK = 1024 * 1024

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


class InstallError(Exception):
    """A problem the user can act on; carries English and Chinese text."""

    def __init__(self, en: str, zh: Optional[str] = None):
        super().__init__(en)
        self.en = en
        self.zh = zh or en

    def text(self, chinese: bool) -> str:
        return self.zh if chinese else self.en


class AppInUseError(InstallError):
    """The install folder is locked, normally because FlowBench is running."""


class InstallCancelled(Exception):
    pass


# ---------------------------------------------------------------- payload --

def read_payload_info(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        info = json.load(fh)
    if not isinstance(info, dict) or info.get("app") != APP_NAME:
        raise InstallError("payload.json does not describe a FlowBench payload",
                           "安装数据损坏：payload.json 不是 FlowBench 的安装数据")
    if not isinstance(info.get("version"), str) or not info["version"]:
        raise InstallError("payload.json has no version",
                           "安装数据损坏：payload.json 缺少版本号")
    return info


def safe_member_path(name: str) -> PurePosixPath:
    """Validate an archive member name and return it as a relative path."""
    clean = name.replace("\\", "/")
    path = PurePosixPath(clean)
    if (not clean or clean.startswith("/") or path.is_absolute()
            or re.match(r"^[A-Za-z]:", clean)):
        raise InstallError(f"Unsafe path in payload: {name!r}",
                           f"安装数据包含不安全的路径：{name!r}")
    parts = [p for p in path.parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise InstallError(f"Unsafe path in payload: {name!r}",
                           f"安装数据包含不安全的路径：{name!r}")
    return PurePosixPath(*parts)


def extract_payload(payload: Path, dest: Path, total_bytes: int = 0,
                    progress: Optional[ProgressFn] = None,
                    cancelled: Optional[CancelFn] = None) -> int:
    """Extract regular files and folders only; return the bytes written."""
    dest.mkdir(parents=True, exist_ok=False)
    done = 0
    with tarfile.open(payload, mode="r:*") as archive:
        for member in archive:
            if cancelled and cancelled():
                raise InstallCancelled()
            rel = safe_member_path(member.name)
            target = dest.joinpath(*rel.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise InstallError(f"Unsupported entry in payload: {member.name!r}",
                                   f"安装数据包含不支持的条目：{member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise InstallError(f"Cannot read {member.name!r} from payload",
                                   f"无法从安装数据读取 {member.name!r}")
            with source, open(target, "wb") as out:
                while True:
                    if cancelled and cancelled():
                        raise InstallCancelled()
                    block = source.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total_bytes, str(rel))
    return done


# ------------------------------------------------------------ target dir --

def is_flowbench_dir(path: Path) -> bool:
    return (path / MARKER_NAME).is_file() or (path / APP_EXE).is_file()


def read_marker(path: Path) -> dict:
    try:
        with open(path / MARKER_NAME, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_marker(path: Path, data: dict) -> None:
    tmp = path / (MARKER_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path / MARKER_NAME)


def check_target(target: Path) -> None:
    """Refuse folders an install or uninstall could damage."""
    if not target.is_absolute():
        raise InstallError("The install folder must be a full path.",
                           "安装位置必须是完整路径。")
    if target.parent == target:
        raise InstallError("Choose a folder, not the root of a drive.",
                           "请选择一个文件夹，不能直接装在磁盘根目录。")
    if target.exists() and not target.is_dir():
        raise InstallError(f"{target} is a file, not a folder.",
                           f"{target} 是一个文件，不是文件夹。")
    if target.is_dir() and any(target.iterdir()) and not is_flowbench_dir(target):
        raise InstallError(
            f"{target} already contains other files. "
            "Choose an empty folder or an existing FlowBench folder.",
            f"{target} 里已经有其他文件。请选择空文件夹或已有的 FlowBench 文件夹。")


def _sibling(target: Path, kind: str) -> Path:
    return target.parent / f"{target.name}.{kind}-{secrets.token_hex(4)}"


def cleanup_stale(target: Path) -> None:
    """Remove staging/backup folders left behind by an interrupted run."""
    pattern = re.compile(re.escape(target.name) + r"\.(new|old)-[0-9a-f]{8}$")
    try:
        entries = list(target.parent.iterdir())
    except OSError:
        return
    for entry in entries:
        if pattern.fullmatch(entry.name) and entry.is_dir():
            remove_tree(entry)


def _on_rm_error(func, path, _exc):
    # Read-only files cannot be deleted on Windows until the flag is cleared.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path: Path) -> bool:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_rm_error)
    else:
        shutil.rmtree(path, onerror=_on_rm_error)
    return not path.exists()


def rename_with_retry(src: Path, dst: Path, attempts: int = 10,
                      delay: float = 0.5) -> None:
    """Rename, retrying briefly: antivirus scanners hold new files for a moment."""
    for attempt in range(attempts):
        try:
            os.rename(src, dst)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ----------------------------------------------------------------- install --

class InstallJob:
    """Stage into a sibling folder, then swap it in.

    ``stage`` may take a while and can be cancelled.  ``activate`` is quick and
    can be retried after an :class:`AppInUseError` without extracting again.
    """

    def __init__(self, payload: Path, info: dict, target: Path,
                 rename_attempts: int = 10, rename_delay: float = 0.5):
        self.payload = Path(payload)
        self.info = info
        self.target = Path(target)
        self.staging: Optional[Path] = None
        self.rename_attempts = rename_attempts
        self.rename_delay = rename_delay

    @property
    def version(self) -> str:
        return self.info["version"]

    def stage(self, progress: Optional[ProgressFn] = None,
              cancelled: Optional[CancelFn] = None) -> Path:
        check_target(self.target)
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InstallError(f"Cannot create {self.target.parent}: {exc}",
                               f"无法创建 {self.target.parent}：{exc}") from exc
        cleanup_stale(self.target)
        staging = _sibling(self.target, "new")
        try:
            extract_payload(self.payload, staging,
                            int(self.info.get("unpacked_bytes") or 0),
                            progress, cancelled)
            if not (staging / APP_EXE).is_file():
                raise InstallError(f"The payload does not contain {APP_EXE}.",
                                   f"安装数据里缺少 {APP_EXE}。")
        except PermissionError as exc:
            remove_tree(staging)
            raise InstallError(
                f"No permission to write to {self.target.parent}. "
                "Choose another folder.",
                f"没有权限写入 {self.target.parent}，请换一个文件夹。") from exc
        except BaseException:
            remove_tree(staging)
            raise
        self.staging = staging
        return staging

    def activate(self, marker: Optional[dict] = None) -> None:
        if self.staging is None or not self.staging.is_dir():
            raise InstallError("Nothing staged to install.", "没有可安装的文件。")
        check_target(self.target)
        write_marker(self.staging, marker or {})
        old = None
        if self.target.exists():
            old = _sibling(self.target, "old")
            try:
                rename_with_retry(self.target, old, self.rename_attempts,
                                  self.rename_delay)
            except OSError as exc:
                raise AppInUseError(
                    f"{self.target} is in use. Close FlowBench and try again.",
                    f"{self.target} 正在被使用，请先退出 FlowBench 再重试。") from exc
        try:
            rename_with_retry(self.staging, self.target, self.rename_attempts,
                              self.rename_delay)
        except OSError as exc:
            if old is not None:
                try:
                    os.rename(old, self.target)
                except OSError:
                    pass
            raise InstallError(f"Could not move the new files into place: {exc}",
                               f"无法把新文件放到安装位置：{exc}") from exc
        self.staging = None
        if old is not None:
            remove_tree(old)

    def discard(self) -> None:
        if self.staging is not None:
            remove_tree(self.staging)
            self.staging = None


# --------------------------------------------------------------- uninstall --

def uninstall_files(target: Path) -> None:
    """Delete an installed FlowBench folder (user data lives elsewhere)."""
    target = Path(target)
    check_target(target)
    if not target.exists():
        return
    if not is_flowbench_dir(target):
        raise InstallError(f"{target} does not look like a FlowBench installation.",
                           f"{target} 看起来不是 FlowBench 的安装目录。")
    old = _sibling(target, "old")
    try:
        # The uninstaller's own bootloader may need a moment to exit.
        rename_with_retry(target, old, attempts=20, delay=0.25)
    except OSError as exc:
        raise AppInUseError(f"{target} is in use. Close FlowBench and try again.",
                            f"{target} 正在被使用，请先退出 FlowBench 再重试。") from exc
    remove_tree(old)
