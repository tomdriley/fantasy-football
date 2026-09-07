import pathlib
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from ffopt.jobs import IdempotencyConflict, JobRunner, JobStore, QueueFull


def wait_for(test, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    test.fail("background operation did not reach expected state")


class TestJobs(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = pathlib.Path(folder.name) / "jobs.sqlite3"
        self.store = JobStore(self.path, capacity=3)

    def test_idempotency_reuses_job_and_rejects_different_payload(self):
        first = self.store.enqueue("collect", {"refresh": False}, "same-key")
        again = self.store.enqueue("collect", {"refresh": False}, "same-key")
        self.assertEqual(first["id"], again["id"])
        with self.assertRaises(IdempotencyConflict):
            self.store.enqueue("collect", {"refresh": True}, "same-key")
        self.assertEqual(self.store.stats()["queued"], 1)

    def test_capacity_is_atomic_across_concurrent_requests(self):
        def submit(i):
            try:
                return self.store.enqueue("collect", {"number": i})["id"]
            except QueueFull:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual(sum(r is not None for r in results), 3)
        self.assertEqual(self.store.stats()["queued"], 3)

    def test_queue_and_results_survive_reopening(self):
        job = self.store.enqueue("evaluate", {"snapshot_id": "snapshot"})
        reopened = JobStore(self.path)
        self.assertEqual(reopened.get(job["id"])["status"], "queued")
        claimed = reopened.claim()
        self.assertEqual(claimed["id"], job["id"])
        reopened.finish(job["id"], result={"evaluation_id": "done"})
        self.assertEqual(self.store.get(job["id"])["result"], {"evaluation_id": "done"})
        with self.assertRaises(ValueError):
            self.store.finish(job["id"], error="cannot overwrite final state")

    def test_restart_marks_interrupted_work_failed_and_runs_queued_work(self):
        interrupted = self.store.enqueue("collect", {})
        self.store.claim()
        queued = self.store.enqueue("collect", {})
        runner = JobRunner(self.store, lambda job: {"ok": "yes"})
        runner.start()
        self.addCleanup(runner.stop)
        wait_for(self, lambda: self.store.get(queued["id"])["status"] == "succeeded")
        failed = self.store.get(interrupted["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("interrupted", failed["error"])

    def test_worker_failure_is_persisted_and_queue_keeps_running(self):
        failed = self.store.enqueue("collect", {"fail": True})
        good = self.store.enqueue("collect", {})
        def run(job):
            if job["payload"].get("fail"):
                raise RuntimeError("test failure")
            return {"ok": "yes"}
        runner = JobRunner(self.store, run)
        with self.assertLogs("ffopt.jobs", level="ERROR"):
            runner.start()
            try:
                wait_for(self, lambda: self.store.get(good["id"])["status"] == "succeeded")
            finally:
                runner.stop()
        self.assertEqual(self.store.get(failed["id"])["status"], "failed")
        self.assertEqual(self.store.get(failed["id"])["error"], "test failure")

    def test_two_api_runners_do_not_execute_or_recover_each_others_active_job(self):
        entered, release = threading.Event(), threading.Event()
        first = self.store.enqueue("collect", {"block": True})
        calls = []
        def handler(job):
            calls.append(job["id"])
            if job["payload"].get("block"):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test release timeout")
            return {"ok": "yes"}
        a = JobRunner(self.store, handler)
        b = JobRunner(JobStore(self.path), handler)
        a.start()
        try:
            self.assertTrue(entered.wait(2))
            b.start()
            second = self.store.enqueue("collect", {})
            time.sleep(0.1)
            self.assertEqual(self.store.get(first["id"])["status"], "running")
            self.assertEqual(self.store.get(second["id"])["status"], "queued")
            release.set()
            wait_for(self, lambda: self.store.get(second["id"])["status"] == "succeeded")
            self.assertEqual(calls, [first["id"], second["id"]])
        finally:
            release.set()
            a.stop()
            b.stop()
