import concurrent.futures
import json
import unittest
from unittest import mock

import test_legacy_reader_auth as reader_auth
from support.legacy_env import LegacyEnvironment, backend, scheduler
from shared import intake


class IntakeAPI(unittest.TestCase):
    setUp = reader_auth.ReaderAuthContract.setUp
    tearDown = reader_auth.ReaderAuthContract.tearDown
    request = reader_auth.ReaderAuthContract.request
    login = reader_auth.ReaderAuthContract.login

    def issue(self):
        self.login()
        status, _, body = self.request("POST", "/api/admin/clients", {"name": "test-scriptcat"}, cookie=self.cookie)
        self.assertEqual(status, 201)
        return json.loads(body)

    def test_pair_submit_scope_and_revocation(self):
        credential = self.issue()
        headers = {"Authorization": "Bearer " + credential["token"]}
        self.assertEqual(self.request("GET", "/api/v1/tasks")[0], 401)
        self.assertEqual(self.request("GET", "/api/admin/clients", headers=headers)[0], 401)
        self.assertEqual(self.request("POST", "/api/admin/x-auth", {}, headers=headers)[0], 401)
        status, _, body = self.request("POST", "/api/v1/tasks", {"url": "https://twitter.com/exampleuser/status/2001"}, headers=headers)
        self.assertEqual(status, 201)
        task = json.loads(body)["task"]
        status, _, body = self.request("POST", "/api/v1/tasks", {"url": "https://x.com/ExampleUser/status/2001?s=20"}, cookie=self.cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["task"]["job_id"], task["job_id"])
        self.assertTrue(json.loads(body)["duplicate"])
        self.assertNotIn(credential["token"].encode(), (self.env.gdl / "clients.sqlite").read_bytes())
        self.request("DELETE", "/api/admin/clients/" + credential["id"], cookie=self.cookie)
        self.assertEqual(self.request("GET", "/api/v1/tasks", headers=headers)[0], 401)

    def test_website_and_script_share_library_and_download_results(self):
        credential = self.issue()
        headers = {"Authorization": "Bearer " + credential["token"]}
        _, _, body = self.request("POST", "/api/v1/tasks", {"url": "@exampleuser"}, headers=headers)
        jid = json.loads(body)["task"]["job_id"]
        seen = []
        def download(args, timeout=3600):
            seen.append(args)
            return "/downloads/twitter/exampleuser/2001_0.jpg\n", 0, "/downloads/twitter/exampleuser/2001_0.jpg\n"
        with mock.patch.object(scheduler, "run_gallery_dl", side_effect=download):
            scheduler.consume_downloads()
        self.assertEqual(seen[0][-1], "https://x.com/exampleuser/media")
        self.assertIn("1-20", seen[0])
        _, _, body = self.request("GET", "/api/v1/tasks/" + jid, cookie=self.cookie)
        self.assertEqual(json.loads(body)["task"]["status"], "done")
        self.assertEqual(json.loads(body)["task"]["new_media"], 1)
        _, _, body = self.request("GET", "/api/v1/library?url=https%3A%2F%2Fx.com%2Fexampleuser", headers=headers)
        self.assertTrue(json.loads(body)["archived"])
        self.assertEqual(self.request("GET", "/connect", cookie=self.cookie)[0], 200)
        status, _, body = self.request("GET", "/integration/xarchive.user.js")
        self.assertEqual(status, 200)
        self.assertIn(b"==UserScript==", body)

    def test_invalid_input_and_subscription_dedup(self):
        self.login()
        for body in ({"url": "https://evil.test/x.com/exampleuser"}, {"url": "@exampleuser", "limit": True},
                     {"url": "@exampleuser", "limit": 0}, {"url": "@exampleuser", "mode": "unknown"}):
            self.assertEqual(self.request("POST", "/api/v1/tasks", body, cookie=self.cookie)[0], 400)
        for expected in (201, 200):
            self.assertEqual(self.request("POST", "/api/v1/tasks", {"url": "@exampleuser", "mode": "subscribe"}, cookie=self.cookie)[0], expected)
        self.assertEqual(len(backend.ensure_subs()), 1)


class IntakePersistence(unittest.TestCase):
    def test_concurrent_clicks_create_one_task_and_restart_recovers(self):
        with LegacyEnvironment() as env:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: intake.submit(env.gdl, {"url": "@exampleuser"}), range(20)))
            self.assertEqual(len({job["job_id"] for job, _ in results}), 1)
            jid = results[0][0]["job_id"]
            scheduler._update_job(jid, status="running")
            scheduler.recover_interrupted_jobs()
            self.assertEqual(scheduler._find_job(jid)["status"], "queued")
            scheduler.recover_orphaned_queued_jobs()
            self.assertFalse(scheduler.load_trigger())

    def test_history_retention_keeps_active_work(self):
        with LegacyEnvironment() as env:
            repo = intake.LegacyJobRepository(env.gdl / ".jobs.json")
            repo.save([{"job_id": "active", "status": "queued"}]+
                      [{"job_id": str(i), "status": "done"} for i in range(210)])
            self.assertEqual(len(repo.load()), 201)
            self.assertEqual(repo.load()[0]["job_id"], "active")


if __name__ == "__main__":
    unittest.main()
