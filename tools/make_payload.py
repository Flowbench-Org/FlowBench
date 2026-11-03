"""Pack dist/FlowBench (+ the uninstaller) into the installer payload.

Usage: python tools/make_payload.py [--dist dist] [--out build/payload]

Writes ``FlowBench-payload.tar.xz`` and ``payload.json`` for Setup.spec.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flowbench_installer import core  # noqa: E402


def app_version() -> str:
    text = (ROOT / "app" / "services" / "updater.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("APP_VERSION not found in app/services/updater.py")
    return match.group(1)


def collect(app_dir: Path, uninstaller: Path):
    files = []
    for root, dirs, names in os.walk(app_dir):
        dirs.sort()
        for name in sorted(names):
            path = Path(root) / name
            files.append((path, path.relative_to(app_dir).as_posix()))
    if not any(rel == core.APP_EXE for _p, rel in files):
        raise SystemExit(f"{app_dir / core.APP_EXE} is missing; run build_all.ps1 first")
    files = [(p, rel) for p, rel in files
             if rel not in (core.UNINSTALL_EXE, core.MARKER_NAME)]
    files.append((uninstaller, core.UNINSTALL_EXE))
    return files


def build(dist: Path, out: Path, preset: int = 9) -> dict:
    app_dir = dist / core.APP_NAME
    uninstaller = dist / core.UNINSTALL_EXE
    if not uninstaller.is_file():
        raise SystemExit(f"{uninstaller} is missing; build Uninstall.spec first")
    files = collect(app_dir, uninstaller)
    out.mkdir(parents=True, exist_ok=True)
    payload = out / core.PAYLOAD_NAME
    tmp = payload.with_suffix(payload.suffix + ".tmp")
    unpacked = 0
    with tarfile.open(tmp, "w:xz", preset=preset, format=tarfile.PAX_FORMAT) as archive:
        for path, rel in files:
            info = archive.gettarinfo(str(path), arcname=rel)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(path, "rb") as fh:
                archive.addfile(info, fh)
            unpacked += info.size
    os.replace(tmp, payload)
    digest = hashlib.sha256()
    with open(payload, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    info = {"app": core.APP_NAME, "version": app_version(), "files": len(files),
            "unpacked_bytes": unpacked, "payload_bytes": payload.stat().st_size,
            "sha256": digest.hexdigest(),
            "created": dt.datetime.now().isoformat(timespec="seconds")}
    (out / core.PAYLOAD_INFO_NAME).write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", default=str(ROOT / "dist"))
    parser.add_argument("--out", default=str(ROOT / "build" / "payload"))
    parser.add_argument("--preset", type=int, default=9, help="xz level 0-9")
    args = parser.parse_args(argv)
    info = build(Path(args.dist), Path(args.out), args.preset)
    print(f"payload {info['version']}: {info['files']} files, "
          f"{info['unpacked_bytes'] / 1e6:.1f} MB -> {info['payload_bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
