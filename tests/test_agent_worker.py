"""The agent's Python worker (agent/worker.py) against loopback servers. It is
also the native engine's fallback, so these pin semantics both share (ENGINE.md)."""
import threading
import time
import unittest

from agent.worker import HeadlessStressJob, TokenBucket
from tests.loopback import LoopbackServer, http_ok, http_redirect, silent


class AgentWorkerTests(unittest.TestCase):
    def _server(self, handler):
        server = LoopbackServer(handler)
        self.addCleanup(server.close)
        return server

    def _start(self, server, **extra):
        config = {"target": "127.0.0.1", "protocol": "HTTP",
                  "url": f"http://127.0.0.1:{server.port}/", "port": server.port,
                  "rate": 20.0, "duration": 2, "threads": 4, "packet_size": 64,
                  "timeout": 2000, "headers": None}
        config.update(extra)
        reports, done = [], threading.Event()

        def on_report(report):
            reports.append(report)
            done.set()

        job = HeadlessStressJob(config, lambda s: None, on_report)
        self.assertTrue(job.start())
        self.addCleanup(job.stop)
        return job, reports, done

    def test_token_bucket_starts_with_one_token(self):
        self.assertEqual(TokenBucket(20).tokens, 1.0)
        self.assertEqual(TokenBucket(0.5).tokens, 1.0)

    def test_first_second_does_not_burst_past_the_rate(self):
        job, reports, done = self._start(self._server(http_ok))
        self.assertTrue(done.wait(15))
        # 20/s for 2 s is 40 requests plus the starting token. A full starting
        # bucket used to add a whole second's burst on top (about 60).
        self.assertLessEqual(reports[0]["total"], 43)

    def test_redirects_are_not_followed(self):
        elsewhere = self._server(http_ok)
        server = self._server(http_redirect(f"http://127.0.0.1:{elsewhere.port}/"))
        job, reports, done = self._start(server, rate=10.0, duration=1, threads=2)
        self.assertTrue(done.wait(15))
        report = reports[0]
        self.assertGreater(report["total"], 0)
        self.assertEqual(report["success"], report["total"])   # a 302 is below 400
        self.assertEqual(elsewhere.connections, 0, "the load followed the redirect")

    def test_stop_on_a_slow_target_reports_promptly_with_the_real_duration(self):
        # Every thread is stuck on a target that never answers (8 s timeout).
        job, reports, done = self._start(self._server(silent), rate=50.0, duration=30,
                                         threads=6, timeout=8000)
        time.sleep(0.6)
        stopped = time.monotonic()
        job.stop()
        self.assertTrue(done.wait(15))
        # It used to join each thread for 1 s (about 6 s here) and count that
        # wait as test time.
        self.assertLess(time.monotonic() - stopped, 4.0)
        self.assertLess(reports[0]["duration"], 2.0)


if __name__ == "__main__":
    unittest.main()
