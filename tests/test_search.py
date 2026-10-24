import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from xarchive.app import create_app
from xarchive.store import Store


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.client = TestClient(create_app(self.tmp.name, run_worker=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.post('/api/v1/auth/setup', json={'password': 'fixture-search-password'})

    def blogger(self, user, name='', featured=0):
        with self.store.connect() as db:
            self.store.add_blogger(user, db)
            db.execute('UPDATE bloggers SET display_name=?,featured=? WHERE user=?', (name, featured, user))

    def test_cookie_change_ignores_inflight_result_from_previous_account(self):
        self.store.submit('example')
        old = self.store.claim()
        response = self.client.put('/api/v1/settings', json={'cookies': 'auth_token=fixture-replacement'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.setting('x_cookie_revision'), 1)
        self.store.finish(old, error='X 请求限流', retry_after=900)
        self.assertFalse(self.store.setting('x_rate_limit_until', 0))

    def post(self, identifier, user, name='', content='', favorite=0, date='2026-01-01 08:00:00'):
        self.store.execute('''INSERT INTO posts(id,user,display_name,content,favorite,posted_at,url,added)
                              VALUES (?,?,?,?,?,?,?,0)''',
                           (str(identifier), user, name, content, favorite, date, 'https://x.com/' + user + '/status/' + str(identifier)))

    def test_at_handle_current_and_historical_names_find_whole_blogger_archive(self):
        self.blogger('example', '现在的名字')
        self.blogger('other', '另一个人')
        self.post(101, 'example', '过去的名字')
        self.post(102, 'example', '另一段旧名')
        self.post(103, 'other', '另一个人')
        for query in ('@EXAMPLE', '现在的名字', '过去的名字'):
            with self.subTest(query=query):
                posts = self.client.get('/api/v1/posts', params={'q': query}).json()
                self.assertEqual([p['id'] for p in posts['items']], ['102', '101'])
                self.assertEqual(posts['total'], 2)
                self.assertEqual(posts['blogger_total'], 1)
                self.assertEqual(posts['bloggers'][0]['user'], 'example')
                bloggers = self.client.get('/api/v1/bloggers', params={'q': query}).json()
                self.assertEqual([b['user'] for b in bloggers['items']], ['example'])

    def test_search_keeps_body_tags_and_notes_and_treats_wildcards_literally(self):
        self.blogger('foo_bar')
        self.blogger('fooxbar')
        self.post(101, 'foo_bar', content='文字 50%')
        self.post(102, 'fooxbar', content='文字 500')
        self.store.execute("UPDATE posts SET tags='标签',note='备注' WHERE id='101'")
        for query in ('foo_bar', '%', '标签', '备注'):
            with self.subTest(query=query):
                result = self.client.get('/api/v1/posts', params={'q': query}).json()
                self.assertEqual([p['id'] for p in result['items']], ['101'])
        self.assertEqual([b['user'] for b in self.client.get('/api/v1/bloggers', params={'q': 'foo_bar'}).json()['items']], ['foo_bar'])
        self.assertEqual(self.client.get('/api/v1/posts', params={'q': "' OR 1=1 --"}).json()['total'], 0)

    def test_exact_handle_ranks_first_and_explicit_user_is_normalized(self):
        self.blogger('foo', '名称')
        self.blogger('foomore', '名称', featured=1)
        self.post(101, 'foo')
        self.post(102, 'foomore')
        result = self.client.get('/api/v1/bloggers', params={'q': '@FOO'}).json()
        self.assertEqual([b['user'] for b in result['items']], ['foo', 'foomore'])
        self.assertEqual(result['total'], 2)
        self.assertEqual(self.client.get('/api/v1/bloggers', params={'user': '@FOO'}).json()['total'], 1)
        result = self.client.get('/api/v1/posts', params={'user': '@FOO'}).json()
        self.assertEqual([p['id'] for p in result['items']], ['101'])
        self.assertEqual(result['blogger_total'], 1)

    def test_summary_has_archive_plan_persistent_success_and_full_blogger_activity(self):
        self.blogger('example', '现在的名字', featured=1)
        self.post(101, 'example', favorite=1)
        self.post(102, 'example', date='2026-01-02 09:00:00')
        for n in range(3):
            self.store.execute('INSERT INTO media(post_id,path,kind,size) VALUES (?,?,?,10)', ('101', 'fixture' + str(n) + '.jpg', 'image'))
        self.store.execute("UPDATE bloggers SET last_success_at=100,note='保留备注' WHERE user='example'")
        self.store.execute("INSERT INTO subscriptions(user,enabled,schedule,next_run) VALUES ('example',0,'quarterly',9999)")
        completed, _ = self.store.submit('example')
        self.store.execute("UPDATE tasks SET status='succeeded',finished=100 WHERE id=?", (completed['id'],))
        active, _ = self.store.submit('example')
        single, _ = self.store.submit('https://x.com/example/status/103')
        self.store.execute("UPDATE tasks SET status='running' WHERE id=?", (single['id'],))
        self.store.delete_task(completed['id'])
        row = self.client.get('/api/v1/bloggers', params={'q': 'example'}).json()['items'][0]
        self.assertEqual((row['post_count'], row['media_count'], row['favorite_count']), (2, 3, 1))
        self.assertEqual((row['schedule'], row['subscribed'], row['enabled'], row['next_run']), ('quarterly', 0, 0, 9999))
        self.assertEqual((row['last_success_at'], row['last_post_at']), (100, '2026-01-02 09:00:00'))
        self.assertEqual((row['note'], row['featured']), ('保留备注', 1))
        self.assertEqual(row['active_task'], {'id': active['id'], 'status': 'queued', 'next_run': active['next_run'], 'source': 'website', 'error': None})

    def test_summary_without_plan_or_success_has_explicit_empty_values(self):
        self.blogger('empty', '没有归档')
        result = self.client.get('/api/v1/bloggers', params={'q': '没有归档'}).json()
        self.assertEqual(result['total'], 1)
        row = result['items'][0]
        self.assertEqual((row['post_count'], row['media_count'], row['favorite_count'], row['enabled'], row['subscribed']), (0, 0, 0, 0, 0))
        for field in ('schedule', 'next_run', 'last_success_at', 'last_post_at', 'active_task'):
            self.assertIsNone(row[field], field)

    def test_post_search_caps_summaries_but_reports_full_blogger_count(self):
        for n in range(25):
            self.blogger('match' + str(n), featured=int(n == 0))
        posts = self.client.get('/api/v1/posts', params={'q': 'match'}).json()
        self.assertEqual((len(posts['bloggers']), posts['blogger_total']), (20, 25))
        self.assertEqual(posts['total'], 0)
        self.assertEqual(self.client.get('/api/v1/bloggers', params={'q': 'match', 'featured': True}).json()['total'], 1)
        unfiltered = self.client.get('/api/v1/posts').json()
        self.assertNotIn('bloggers', unfiltered)
        self.assertEqual(self.client.get('/api/v1/bloggers').json()['total'], 25)

    def test_search_remains_private(self):
        token = self.client.post('/api/v1/clients', json={'name': 'fixture-search-client'}).json()['token']
        self.client.cookies.clear()
        for path in ('/api/v1/posts?q=example', '/api/v1/bloggers?q=example'):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={'Authorization': 'Bearer ' + token}).status_code, 403)

    def test_new_or_cleared_cookie_removes_cooldown_but_empty_input_keeps_it(self):
        self.store.set_setting('x_rate_limit_until', 9999999999)
        self.assertEqual(self.client.put('/api/v1/settings', json={'cookies': None}).status_code, 200)
        self.assertEqual(self.store.setting('x_rate_limit_until'), 9999999999)
        for payload in ({'cookies': 'auth_token=fixture-new-cookie'}, {'clear_cookies': True}):
            self.store.set_setting('x_rate_limit_until', 9999999999)
            self.assertEqual(self.client.put('/api/v1/settings', json=payload).status_code, 200)
            self.assertIsNone(self.store.setting('x_rate_limit_until'))

    def test_cookie_change_rolls_back_if_cooldown_clear_cannot_commit(self):
        self.store.set_setting('x_rate_limit_until', 9999999999)
        self.store.set_setting('x_cookies', {'auth_token': 'fixture-old-cookie'})
        self.store.execute("""CREATE TRIGGER reject_cooldown_delete BEFORE DELETE ON settings
                            WHEN OLD.key='x_rate_limit_until' BEGIN SELECT RAISE(ABORT,'fixture failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.put('/api/v1/settings', json={'cookies': 'auth_token=fixture-new-cookie'})
        self.assertEqual(self.store.setting('x_cookies'), {'auth_token': 'fixture-old-cookie'})
        self.assertEqual(self.store.setting('x_rate_limit_until'), 9999999999)


if __name__ == '__main__':
    unittest.main()
