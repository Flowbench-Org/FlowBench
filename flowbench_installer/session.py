"""One install or update run: stage, swap in, then register with Windows."""
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


def _same_dir(a, b) -> bool:
    if not a:
        return False
    import os
    return (os.path.normcase(os.path.abspath(str(a)))
            == os.path.normcase(os.path.abspath(str(b))))
