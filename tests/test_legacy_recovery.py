"""C9: startup recovery contracts using only disposable JSON state."""
import json
import unittest

from support.legacy_env import LegacyEnvironment, scheduler


def job(jid, status, kind=None):
    value = {"job_id": jid, "sub_id": "s1", "user": "exampleuser", "status": status, "ts_submit": 1}
    if kind:
        value["kind"] = kind
    return value


class RecoveryContract(unittest.TestCase):
    def test_queued_with_trigger_remains_one_queue_entry(self):
        with LegacyEnvironment() as env:
            env.write_jobs([job("j1", "queued")])
            env.write_triggers([{"id": "s1", "job": "j1", "user": "exampleuser"}])
            scheduler.recover_interrupted_jobs()
            scheduler.recover_orphaned_queued_jobs()
            self.assertEqual(scheduler._find_job("j1")["status"], "queued")
            self.assertEqual([t["job"] for t in scheduler.load_trigger()], ["j1"])

    def test_missing_trigger_restored_once(self):
        with LegacyEnvironment() as env:
            env.write_jobs([job("j1", "queued")])
            scheduler.recover_orphaned_queued_jobs()
            scheduler.recover_orphaned_queued_jobs()
            self.assertEqual([t["job"] for t in scheduler.load_trigger()], ["j1"])

    def test_running_with_trigger_requeued_on_restart(self):
        with LegacyEnvironment() as env:
            env.write_jobs([job("j1", "running")])
            env.write_triggers([{"id": "s1", "job": "j1"}])
            scheduler.recover_interrupted_jobs()
            state = scheduler._find_job("j1")
            self.assertEqual(state["status"], "queued")
            self.assertIsNone(state["started_ts"])
            self.assertEqual([t["job"] for t in scheduler.load_trigger()], ["j1"])

    def test_running_without_trigger_failed_known_legacy_issue_dec020(self):
        with LegacyEnvironment() as env:
            env.write_jobs([job("j1", "running")])
            scheduler.recover_interrupted_jobs()
            self.assertEqual(scheduler._find_job("j1")["status"], "failed")
            self.assertEqual(scheduler.load_trigger(), [])

    def test_comment_restart_uses_different_queue_known_legacy_issue(self):
        with LegacyEnvironment() as env:
            env.write_jobs([job("comment", "running", kind="comments")])
            (env.gdl / ".ctrigger.json").write_text(json.dumps([{"job": "comment", "tid": "2001", "user": "exampleuser"}]), encoding="utf-8")
            scheduler.recover_interrupted_jobs()
            scheduler.recover_orphaned_queued_jobs()
            self.assertEqual(scheduler._find_job("comment")["status"], "failed")
            self.assertEqual([x["job"] for x in scheduler.load_ctriggers()], ["comment"])
            self.assertEqual(scheduler.load_trigger(), [])

    def test_queued_comment_not_recovered_into_media_queue(self):
        with LegacyEnvironment() as env:
            env.write_jobs([job("comment", "queued", kind="comments")])
            scheduler.recover_orphaned_queued_jobs()
            self.assertEqual(scheduler.load_trigger(), [])


if __name__ == "__main__":
    unittest.main()
