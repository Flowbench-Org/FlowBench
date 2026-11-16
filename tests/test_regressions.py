import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-regression-tests"))

from PySide6.QtWidgets import QApplication

from app.services.auth import build_http_url, normalize_host
from app.services.stress import StressEngine
from app.ui.busy_overlay import BusyOverlay
from server_agent import validate_job


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_http_url_uses_selected_scheme_and_preserves_path(self):
        self.assertEqual(
            build_http_url("http://example.test/health?q=1", "example.test", 443, "HTTPS"),
            "https://example.test/health?q=1",
        )

    def test_targets_that_parsers_read_differently_are_refused(self):
        # urlsplit takes example.test as the host; the engines would send to 127.0.0.2.
        self.assertEqual(normalize_host("http://127.0.0.2:8080\\@example.test/"), "")
        self.assertEqual(normalize_host("exam\tple.test"), "")
        self.assertEqual(normalize_host("example.test\x00"), "")
        self.assertEqual(normalize_host("http://example.test:8080/a?b=c"), "example.test")

    def test_stress_engine_rejects_invalid_or_non_finite_config(self):
        engine = StressEngine()
        base = {
            "target": "example.test", "protocol": "HTTP", "threads": 1,
            "rate": 1, "duration": 1, "packet_size": 64, "timeout": 1000,
        }
        self.assertTrue(engine.start({**base, "threads": "1"}))
        engine.stop()
        self.assertFalse(StressEngine().start({**base, "rate": float("nan")}))
        self.assertFalse(StressEngine().start({**base, "threads": True}))

    def test_busy_overlay_clears_previous_detail_text(self):
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.resize(400, 300)
        overlay = BusyOverlay(parent)
        overlay.show("First", "old detail")
        overlay.set_text("Second")
        self.assertFalse(overlay._sub_label.isVisible())
        self.assertEqual(overlay._sub_label.text(), "")
        overlay.hide()
        parent.deleteLater()

    def test_agent_rejects_boolean_numbers_and_wrong_http_scheme(self):
        config = {
            "allowed_targets": ["example.test"],
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 20, "max_duration": 60, "max_threads": 4,
            "max_packet_size": 4096,
        }
        with self.assertRaises(ValueError):
            validate_job({"target": "example.test", "protocol": "HTTP", "port": True}, config)
        with self.assertRaises(ValueError):
            validate_job({
                "target": "example.test", "protocol": "HTTPS",
                "url": "http://example.test/health",
            }, config)
    def test_oserr_classification_uses_errno_on_localized_windows(self):
        from app.services.stress import _classify_conn_error, _oserr_str

        class _Err(Exception):
            def __init__(self, msg, errno=None):
                super().__init__(msg)
                self.errno = errno

        # Chinese Windows: message text alone would classify as timeout.
        self.assertEqual(_oserr_str(_Err("由于连接方在一段时间后没有正确答复或连接的主机没有反应，连接尝试失败。", 10060)), "timeout")
        self.assertEqual(_oserr_str(_Err("由于目标计算机积极拒绝，无法连接。", 10061)), "refused")
        self.assertEqual(_oserr_str(_Err("远程主机强迫关闭了一个现有的连接。", 10054)), "reset")
        # POSIX errno
        self.assertEqual(_oserr_str(_Err("Connection refused", 111)), "refused")
        self.assertEqual(_oserr_str(_Err("No route to host", 113)), "unreachable")
        # Unknown errno stays stable
        self.assertEqual(_oserr_str(_Err("weird", 12345)), "errno_12345")
        # Windows connect-refused message must not be misread as timeout
        self.assertEqual(
            _classify_conn_error(_Err("A connection attempt failed because the connected party did not properly respond")),
            "refused")



if __name__ == "__main__":
    unittest.main()
