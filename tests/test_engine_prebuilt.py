"""The prebuilt Linux engine that generated node bundles ship (engine/prebuilt)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tools import engine_source_hash

ROOT = Path(__file__).resolve().parents[1]
PREBUILT = ROOT / "engine" / "prebuilt" / "linux-x86_64" / "flowbench-engine"


class PrebuiltEngineTests(unittest.TestCase):
    def test_prebuilt_engine_was_built_from_the_current_sources(self):
        self.assertTrue(PREBUILT.is_file(), PREBUILT)
        self.assertEqual(
            engine_source_hash.recorded_hash(), engine_source_hash.source_hash(),
            "the engine sources changed but engine/prebuilt/linux-x86_64 was not rebuilt; "
            "rebuild it as engine/prebuilt/README.md says, then run "
            "tools/engine_source_hash.py --write")

    def test_prebuilt_engine_is_a_linux_x86_64_executable(self):
        head = PREBUILT.read_bytes()[:20]
        self.assertEqual(head[:4], b"\x7fELF")
        self.assertEqual(head[4], 2)                                    # 64-bit
        self.assertEqual(int.from_bytes(head[18:20], "little"), 0x3E)  # x86-64

    def test_source_hash_ignores_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            hashes = []
            for newline in ("\n", "\r\n"):
                engine = Path(tmp) / newline.encode().hex()
                (engine / "src").mkdir(parents=True)
                for name, text in (("Cargo.toml", "[package]\nname = 'x'\n"),
                                   ("Cargo.lock", "version = 4\n"),
                                   ("src/main.rs", "fn main() {\n}\n")):
                    (engine / name).write_bytes(text.replace("\n", newline).encode())
                hashes.append(engine_source_hash.source_hash(engine))
            self.assertEqual(hashes[0], hashes[1])

    def test_desktop_build_and_node_bundles_fall_back_to_it(self):
        spec = (ROOT / "FlowBench.spec").read_text(encoding="utf-8")
        self.assertIn("engine/prebuilt/linux-x86_64/flowbench-engine", spec)
        from app.ui.agent_view import AgentView
        with tempfile.TemporaryDirectory() as linux_root, \
             mock.patch.dict(os.environ, {"FLOWBENCH_LINUX_ENGINE": ""}), \
             mock.patch.object(Path, "is_file", lambda path: path.resolve() == PREBUILT.resolve()):
            found = AgentView._linux_engine_path(linux_root)
        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), PREBUILT.resolve())


if __name__ == "__main__":
    unittest.main()
