"""The real native engine binary against loopback servers (127.0.0.1 only).

Skipped unless a built engine is found: ``FLOWBENCH_ENGINE``, or
``engine/target/release`` after build_engine.ps1 / build_engine.sh. These pin
the semantics ENGINE.md says the engine shares with the Python workers.
"""
import os
import threading
import time
import unittest
from unittest import mock

from agent.engine_bridge import RustStressJob, find_engine_binary
from tests.loopback import (LoopbackServer, http_ok, http_slow_body, http_truncated,
                            silent, tcp_reset_after_data)

BINARY = find_engine_binary()


@unittest.skipUnless(BINARY, "the native engine is not built")
class NativeEngineBinaryTests(unittest.TestCase):
    def _server(self, handler):
        server = LoopbackServer(handler)
        self.addCleanup(server.close)
        return server

    def _start(self, config):
        reports, done = [], threading.Event()

        def on_report(report):
            reports.append(report)
            done.set()

        job = RustStressJob(config, lambda s: None, on_report, BINARY)
        self.assertTrue(job.start(), "the engine did not start")
        self.addCleanup(job.stop)
        return job, reports, done

    def _run(self, config):
        job, reports, done = self._start(config)
        self.assertTrue(done.wait(20), "no report")
        return reports[0]

    @staticmethod
    def _http(server, **extra):
        config = {"target": "127.0.0.1", "protocol": "HTTP",
                  "url": f"http://127.0.0.1:{server.port}/", "port": server.port,
                  "rate": 10.0, "duration": 1, "threads": 2, "packet_size": 64,
                  "timeout": 2000, "headers": None}
        config.update(extra)
        return config

    def test_truncated_body_is_a_failure(self):
        report = self._run(self._http(self._server(http_truncated)))
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["success"], 0)
        self.assertEqual(report["fail"], report["total"])

    def test_body_that_keeps_arriving_is_not_cut_off_at_the_timeout(self):
        # Six chunks 0.2 s apart take at least 1.2 s, longer than the 1 s
        # timeout, but no single read waits that long (requests' per-read
        # timeout). A whole-request timeout would end each one at about 1.0 s.
        report = self._run(self._http(self._server(http_slow_body(6, 0.2)),
                                      rate=4.0, threads=4, timeout=1000))
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["success"], report["total"])
        self.assertGreater(report["p50"], 1150)

    def test_stalled_response_times_out(self):
        report = self._run(self._http(self._server(silent), rate=4.0, timeout=500))
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["errors"].get("timeout"), report["total"])

    def test_tcp_reset_after_the_send_is_a_failure(self):
        server = self._server(tcp_reset_after_data)
        report = self._run({"target": "127.0.0.1", "protocol": "TCP", "url": "",
                            "port": server.port, "rate": 10.0, "duration": 1, "threads": 2,
                            "packet_size": 64, "timeout": 1000, "headers": None})
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["success"], 0)
        self.assertEqual(report["errors"].get("reset"), report["total"])

    def test_first_second_does_not_burst_past_the_rate(self):
        report = self._run(self._http(self._server(http_ok), rate=20.0, duration=2, threads=4))
        self.assertGreater(report["total"], 0)
        self.assertLessEqual(report["total"], 43)   # 40 + the starting token

    def test_stop_is_prompt_when_every_slot_is_busy(self):
        job, reports, done = self._start(self._http(self._server(silent), rate=50.0,
                                                    duration=30, threads=3, timeout=8000))
        time.sleep(0.6)
        stopped = time.monotonic()
        job.stop()
        self.assertTrue(done.wait(15), "no report after stop")
        self.assertLess(time.monotonic() - stopped, 4.0)
        self.assertLess(reports[0]["duration"], 2.0)

    def test_url_whose_host_is_not_the_target_is_refused(self):
        server = self._server(http_ok)
        for url in (f"http://127.0.0.1:{server.port}/",
                    # urlsplit reads "localhost" here; the engine would send to 127.0.0.1.
                    f"http://127.0.0.1:{server.port}\\@localhost:{server.port}/"):
            report = self._run(dict(self._http(server), target="localhost", url=url))
            self.assertEqual(report["errors"], {"engine": 1}, url)
        self.assertEqual(server.connections, 0, "the engine sent load to a host it was not given")

    def test_bytes_tx_is_the_request_head_on_the_wire(self):
        server = self._server(http_ok)
        report = self._run(self._http(server, url=f"http://127.0.0.1:{server.port}/path?q=1",
                                      headers={"X-Custom": "abc"}))
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["bytes_tx"], report["total"] * len(server.heads[0]))

    def test_environment_proxies_are_ignored(self):
        proxy = self._server(http_ok)
        server = self._server(http_ok)
        address = f"http://127.0.0.1:{proxy.port}"
        env = {name: address for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                          "http_proxy", "https_proxy", "all_proxy")}
        env.update(NO_PROXY="", no_proxy="")
        with mock.patch.dict(os.environ, env):
            report = self._run(self._http(server))
        self.assertGreater(report["success"], 0)
        self.assertEqual(proxy.connections, 0, "the load went through an environment proxy")


if __name__ == "__main__":
    unittest.main()
