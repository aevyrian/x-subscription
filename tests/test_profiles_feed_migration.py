import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from xarchive.app import create_app
from xarchive.avatars import avatar_file, avatar_url_ok
from xarchive.library import safe_file, list_posts
from xarchive.migrate import import_legacy
from xarchive.store import Store


class ProfileFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'data')

    def tearDown(self):
        self.tmp.cleanup()

    def test_random_pages_are_stable_and_exclude_new_arrivals(self):
        for n in range(70):
            self.store.execute('INSERT INTO posts(id,user,url,added) VALUES (?,?,?,?)', (str(n+100),'example','https://x.com/example/status/'+str(n+100),time.time()))
        first = list_posts(self.store,order='random',seed='one')
        second = list_posts(self.store,order='random',seed='one',offset=30,snapshot=first['snapshot'])
        a = [p['id'] for p in first['items']]
        self.assertEqual(a,[p['id'] for p in list_posts(self.store,order='random',seed='one',snapshot=first['snapshot'])['items']])
        self.assertFalse(set(a)&{p['id'] for p in second['items']})
        self.assertNotEqual(a,[p['id'] for p in list_posts(self.store,order='random',seed='two')['items']])
        self.store.execute("INSERT INTO posts(id,user,url,added) VALUES ('new','example','url',?)", (first['snapshot']+1,))
        self.assertEqual(list_posts(self.store,order='random',seed='one',snapshot=first['snapshot'])['total'],70)

    def test_time_sort_and_plan_star_preserve_schedule(self):
        self.store.subscribe('example','weekly')
        for identifier in ('10','9','100'):
            self.store.execute('INSERT INTO posts(id,user,url,added) VALUES (?,?,?,?)',(identifier,'example','url',time.time()))
        with TestClient(create_app(self.store.root,run_worker=False)) as client:
            client.post('/api/v1/auth/setup',json={'password':'test-only-password'})
            for order,expected in [('timeline',['100','10','9']),('oldest',['9','10','100'])]:
                first=client.get('/api/v1/posts',params={'order':order,'limit':2}).json()
                second=client.get('/api/v1/posts',params={'order':order,'limit':2,'offset':2,'snapshot':first['snapshot']}).json()
                self.assertEqual([p['id'] for p in first['items']+second['items']],expected)
            self.assertEqual(client.get('/api/v1/posts?order=invalid').status_code,422)
            client.patch('/api/v1/bloggers/example',json={'featured':True,'display_name':'例子'})
            plan=client.get('/api/v1/subscriptions').json()['items'][0]
            self.assertEqual((plan['featured'],plan['schedule'],plan['display_name']),(1,'weekly','例子'))
            client.patch('/api/v1/bloggers/example',json={'featured':False})
            plan=client.get('/api/v1/subscriptions').json()['items'][0]
            self.assertEqual((plan['featured'],plan['schedule']),(0,'weekly'))

    def test_featured_patch_preserves_name_and_note(self):
        self.store.submit('example')
        self.store.execute("UPDATE bloggers SET display_name='保留名称',note='保留备注'")
        with TestClient(create_app(self.store.root,run_worker=False)) as client:
            client.post('/api/v1/auth/setup',json={'password':'test-only-password'})
            self.assertEqual(client.patch('/api/v1/bloggers/example',json={'featured':True}).status_code,200)
            blogger=client.get('/api/v1/bloggers?featured=true').json()['items'][0]
            self.assertEqual((blogger['display_name'],blogger['note'],blogger['featured']),('保留名称','保留备注',1))
            self.assertEqual(client.get('/api/v1/avatars/example').headers['content-type'],'image/svg+xml')
            client.cookies.clear()
            self.assertEqual(client.get('/api/v1/avatars/example').status_code,401)

    def test_legacy_import_keeps_sources_and_is_repeatable(self):
        base=Path(self.tmp.name)/'legacy'; folder=base/'Example';folder.mkdir(parents=True)
        url='https://pbs.twimg.com/profile_images/123/avatar.jpg'
        metadata={'tweet_id':123,'author':{'name':'Example','nick':'例子','profile_image':url},'content':'旧帖子'}
        (folder/'Example_123.json').write_text(json.dumps(metadata),encoding='utf-8')
        (folder/'123_1.jpg').write_bytes(b'fixture-image')
        subs=base/'subscriptions.json';subs.write_text(json.dumps({'subs':[{'user':'Example','frequency':'weekly','enabled':True},{'user':'Other','frequency':'once','enabled':True}]}))
        history=base/'history.sqlite'
        with closing(sqlite3.connect(history)) as db, db:
            db.execute('CREATE TABLE archive(entry TEXT PRIMARY KEY)');db.execute("INSERT INTO archive VALUES ('twitter_123_1')")
        favorites=base/'favorites.json';favorites.write_text('["Example"]')
        config=base/'config.json';config.write_text(json.dumps({'extractor':{'proxy':'http://proxy.example:7890'}}))
        result=import_legacy(self.store,base,subs,history,favorites,config=config)
        self.assertEqual(self.store.setting('x_proxy'),'http://proxy.example:7890')
        self.assertEqual(result['totals'],{'posts':1,'media':1,'bloggers':2,'subscriptions':2})
        self.assertEqual(self.store.query('SELECT avatar_url,featured FROM bloggers WHERE user=?',('example',))[0],{'avatar_url':url,'featured':1})
        self.assertEqual({s['user']:(s['schedule'],s['enabled']) for s in self.store.query('SELECT * FROM subscriptions')},{'example':('weekly',1),'other':('once',0)})
        self.assertEqual(import_legacy(self.store,base,subs,history,favorites)['posts'],0)
        self.assertTrue((folder/'Example_123.json').exists())
        self.assertEqual(safe_file(self.store,'@legacy/Example/123_1.jpg').read_bytes(),b'fixture-image')
        self.assertFalse(any(self.store.downloads.iterdir()))
        with self.assertRaises(ValueError):
            safe_file(self.store,'@legacy/../data/config/archive.sqlite3')
        self.assertEqual(self.store.query('SELECT count(*) n FROM tasks')[0]['n'],0)

    def test_avatar_validation_and_cached_local_file(self):
        self.assertTrue(avatar_url_ok('https://pbs.twimg.com/profile_images/1/a.jpg'))
        for value in ('http://pbs.twimg.com/profile_images/a','https://evil.test/a','https://pbs.twimg.com:999/a','https://name@pbs.twimg.com/profile_images/a',{},'https://pbs.twimg.com/x'):
            self.assertFalse(avatar_url_ok(value))
        self.store.submit('example')
        self.store.execute("UPDATE bloggers SET avatar_url='https://pbs.twimg.com/profile_images/1/a.jpg'")
        from email.message import Message
        headers=Message();headers['Content-Type']='image/jpeg'
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'\xff\xd8\xfffixture'
        reply=Reply();reply.headers=headers
        with patch('urllib.request.OpenerDirector.open',return_value=reply) as fetch:
            path=avatar_file(self.store,'example')
            self.assertEqual(path.read_bytes(),b'\xff\xd8\xfffixture')
            self.assertEqual(avatar_file(self.store,'example'),path)
            self.assertEqual(fetch.call_count,1)


if __name__=='__main__':
    unittest.main()
