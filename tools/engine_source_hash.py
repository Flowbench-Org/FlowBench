"""Hash the native engine's sources, so a prebuilt binary can be checked
against the code it was built from.

    python tools/engine_source_hash.py            # print the hash
    python tools/engine_source_hash.py --write    # record it in engine/prebuilt
    python tools/engine_source_hash.py --check    # fail if the prebuilt is stale
"""
import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
RECORD = ENGINE / "prebuilt" / "linux-x86_64" / "SOURCE.sha256"


def source_files(engine=ENGINE):
    return [engine / "Cargo.toml", engine / "Cargo.lock",
            *sorted((engine / "src").rglob("*.rs"))]


def source_hash(engine=ENGINE):
    digest = hashlib.sha256()
    for path in source_files(engine):
        # Line endings depend on the checkout (core.autocrlf on Windows); the
        # code does not.
        digest.update(path.relative_to(engine).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def recorded_hash(record=RECORD):
    try:
        return record.read_text(encoding="ascii").split()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hash the native engine's sources.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="record the hash for engine/prebuilt")
    mode.add_argument("--check", action="store_true", help="fail if engine/prebuilt is stale")
    args = parser.parse_args(argv)
    current = source_hash()
    if args.write:
        RECORD.write_text(current + "\n", encoding="ascii")
    elif args.check and recorded_hash() != current:
        print("engine/prebuilt was built from other sources; rebuild it "
              "(engine/prebuilt/README.md)", file=sys.stderr)
        return 1
    print(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
