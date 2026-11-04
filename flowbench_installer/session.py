"""One install, update or uninstall run, including the Windows integration."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Callable, List, Optional

from flowbench_installer import core, winplat

Progress = Callable[[int, str], None]   # percent 0-100, detail text


class SetupSession:
    """Drives :class:`core.InstallJob` plus the Windows integration.

    ``platform`` defaults to :mod:`winplat`; tests pass a fake with the same
    functions.
    """

    STAGE_END = 90       # extraction maps to 0-90 %

    def __init__(self, payload: Path, info: dict, target: Path, *,
                 desktop_shortcut: Optional[bool] = True, update: bool = False,
                 platform=winplat, rename_attempts: int = 10,
                 rename_delay: float = 0.5):
        self.info = info
        self.target = Path(target)
        self.update = update
        self.platform = platform
        self.job = core.InstallJob(payload, info, self.target,
                                   rename_attempts=rename_attempts,
                                   rename_delay=rename_delay)
        self.previous = core.read_marker(self.target) if self.target.is_dir() else {}
        links = platform.shortcut_paths()
        self.start_menu_link = Path(links["start_menu"])
        self.desktop_link = Path(links["desktop"])
        if desktop_shortcut is None:
            # Updates keep the desktop shortcut only if the user still has it.
            desktop_shortcut = self.desktop_link.exists()
        self.desktop_shortcut = bool(desktop_shortcut)
        self.legacy = [] if update else [
            entry for entry in platform.find_legacy_installs()
            if not _same_dir(entry.get("location"), self.target)]
        self.warnings: List[tuple] = []   # (kind, subject, detail)

    # -- checks -------------------------------------------------------------

    @property
    def exe(self) -> Path:
        return self.target / core.APP_EXE

    def blocking_pids(self) -> List[int]:
        """FlowBench processes that must exit before installing."""
        exes = [self.exe] + [Path(e["location"]) / core.APP_EXE
                             for e in self.legacy if e.get("location")]
        return self.platform.running_instances(exes)

    # -- steps --------------------------------------------------------------

    def stage(self, progress: Optional[Progress] = None, cancelled=None) -> None:
        def on_bytes(done, total, name):
            if progress:
                pct = int(done * self.STAGE_END / total) if total else 0
                progress(min(self.STAGE_END, pct), name)
        self.job.stage(on_bytes, cancelled)

    def activate(self, progress: Optional[Progress] = None) -> None:
        if progress:
            progress(self.STAGE_END + 2, "")
        links = [str(self.start_menu_link)]
        if self.desktop_shortcut:
            links.append(str(self.desktop_link))
        self.job.activate({
            "app": core.APP_NAME,
            "version": self.info["version"],
            "installed_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "shortcuts": links,
        })

    def finalize(self, progress: Optional[Progress] = None) -> List[tuple]:
        """Shortcuts, uninstall entry and legacy cleanup; failures become warnings."""
        p = self.platform
        if progress:
            progress(95, "")
        icon = self.exe
        for link, wanted in ((self.start_menu_link, True),
                             (self.desktop_link, self.desktop_shortcut)):
            if wanted:
                try:
                    p.create_shortcut(link, self.exe, self.target, icon,
                                      "FlowBench Network Stress Testing Tool")
                except Exception as exc:
                    self.warnings.append(("shortcut", str(link), str(exc)))
        if not self.desktop_shortcut and not self.update:
            p.remove_files([self.desktop_link])
        try:
            p.write_uninstall_entry(self.target, self.info["version"],
                                    core.folder_size(self.target))
        except Exception as exc:
            self.warnings.append(("registry", "", str(exc)))
        p.notify_shell()
        for entry in self.legacy:
            if progress:
                progress(97, entry.get("location", ""))
            try:
                code = p.run_legacy_uninstall(entry)
            except Exception as exc:
                code = str(exc)
            if code != 0:
                self.warnings.append(("legacy", entry.get("location", ""), str(code)))
        if progress:
            progress(100, "")
        return self.warnings

    def discard(self) -> None:
        self.job.discard()

    def launch_app(self) -> None:
        self.platform.launch_detached(self.exe, (), self.target)


class UninstallSession:
    """Remove an installed FlowBench: files, shortcuts, the Settings → Apps
    entry and, if asked, the user's settings/plugins/logs.

    Used by both the uninstall window and the silent ``/S`` mode.
    """

    FILES_END = 85       # deleting the install folder maps to 0-85 %

    def __init__(self, target: Path, *, remove_user_data: bool = False,
                 data_dir: Optional[Path] = None, parent_pid: int = 0,
                 wait_timeout: float = 30.0, platform=winplat):
        self.target = Path(target)
        self.remove_user_data = remove_user_data
        self.data_dir = Path(data_dir) if data_dir else core.user_data_dir()
        self.parent_pid = int(parent_pid or 0)
        self.wait_timeout = wait_timeout
        self.platform = platform
        self.marker = core.read_marker(self.target)
        self.warnings: List[tuple] = []   # (kind, subject, detail)

    @property
    def version(self) -> str:
        return str(self.marker.get("version") or "")

    def blocking_pids(self) -> List[int]:
        """FlowBench processes that must exit before uninstalling."""
        return self.platform.running_instances([self.target / core.APP_EXE])

    def run(self, progress: Optional[Progress] = None, cancelled=None) -> List[tuple]:
        p = self.platform
        self.warnings = []
        if self.parent_pid:
            # The first-stage uninstaller still has its exe in the folder open.
            p.wait_for_exit(self.parent_pid, self.wait_timeout)
        if progress:
            progress(1, "")

        def on_file(done, total, name):
            if progress:
                progress(1 + int(done * (self.FILES_END - 1) / total) if total else 1, name)

        core.uninstall_files(self.target, on_file)
        if progress:
            progress(self.FILES_END + 3, "")
        self._remove_shortcuts()
        registered = p.registered_install_dir()
        if registered is None or _same_dir(registered, self.target):
            try:
                p.delete_uninstall_entry()
            except Exception as exc:
                self.warnings.append(("registry", "", str(exc)))
        if self.remove_user_data:
            if progress:
                progress(94, str(self.data_dir))
            if not core.remove_user_data(self.data_dir):
                self.warnings.append(("user_data", str(self.data_dir), ""))
        p.notify_shell()
        if progress:
            progress(100, "")
        return self.warnings

    def _remove_shortcuts(self) -> None:
        listed = self.marker.get("shortcuts")
        if not isinstance(listed, list):
            listed = [str(path) for path in self.platform.shortcut_paths().values()]
        # Only ever delete .lnk files, whatever the marker says.
        self.platform.remove_files([path for path in listed
                                    if isinstance(path, str) and path.lower().endswith(".lnk")])


def _same_dir(a, b) -> bool:
    if not a:
        return False
    import os
    return (os.path.normcase(os.path.abspath(str(a)))
            == os.path.normcase(os.path.abspath(str(b))))
