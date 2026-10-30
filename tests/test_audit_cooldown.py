import io
import logging
import tempfile
import time
import unittest
from unittest.mock import patch

from xarchive.engine import Engine
from xarchive.store import Store
from xarchive.worker import Worker


class InterruptedCooldownTests(unittest.TestCase):
    def test_cancel_or_shutdown_preserves_observed_shared_limit(self):
        cases = (('cancel', 'shared', False), ('shutdown', 'shared', False),
                 ('cancel', 'shared', True), ('cancel', 'task', False))
        for action, scope, changed_cookie in cases:
            with self.subTest(action=action, scope=scope, changed_cookie=changed_cookie), tempfile.TemporaryDirectory() as root:
                store = Store(root)
                store.set_setting('x_cookies', {'auth_token': 'fixture'})
                task, _ = store.submit('alice')
                store.submit('bob')
                worker = Worker(store, Engine(store))

                class Process:
                    returncode = None
                    stdout = io.StringIO('XARCHIVE_EVENT {"type":"rate_limit","scope":"' + scope + '","retry_after":900}\n')

                    def poll(self):
                        if self.returncode is None:
                            if changed_cookie:
                                store.set_setting('x_cookie_revision', 1)
                            if action == 'cancel':
                                store.cancel(task['id'])
                            else:
                                worker.stop_event.set()
                        return self.returncode

                    def terminate(self):
                        self.returncode = -15

                    def wait(self, timeout=None):
                        return self.returncode

                with patch('xarchive.engine.subprocess.Popen', return_value=Process()):
                    worker.run_one()

                self.assertEqual(store.query('SELECT status FROM tasks WHERE id=?', (task['id'],))[0]['status'],
                                 'cancelled' if action == 'cancel' else 'retry')
                if scope == 'shared' and not changed_cookie:
                    self.assertGreater(store.setting('x_rate_limit_until', 0), time.time() + 890)
                    self.assertIsNone(Store(root).claim())
                else:
                    self.assertEqual(store.setting('x_rate_limit_until', 0), 0)
                    self.assertEqual(Store(root).claim()['user'], 'bob')

    def test_index_failure_does_not_erase_observed_limit(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.set_setting('x_cookies', {'auth_token': 'fixture'})
            task, _ = store.submit('alice')
            worker = Worker(store, Engine(store))

            class Process:
                returncode = 0
                stdout = io.StringIO('XARCHIVE_EVENT {"type":"rate_limit","scope":"shared","retry_after":900}\n')

                def poll(self):
                    store.cancel(task['id'])
                    return self.returncode

            with patch('xarchive.engine.subprocess.Popen', return_value=Process()), \
                    patch('xarchive.engine.index_folder', side_effect=OSError('sensitive fixture')):
                with self.assertLogs(level=logging.ERROR) as captured:
                    worker.run_one()
            self.assertGreater(store.setting('x_rate_limit_until', 0), time.time() + 890)
            self.assertEqual(store.query('SELECT status FROM tasks WHERE id=?', (task['id'],))[0]['status'], 'cancelled')
            self.assertNotIn('sensitive fixture', '\n'.join(captured.output))


if __name__ == '__main__':
    unittest.main()
