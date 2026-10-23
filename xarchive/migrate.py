"""Import legacy media and subscriptions without modifying their source."""
import argparse
import hashlib
import json
import sqlite3
import shutil
from contextlib import closing
from pathlib import Path

from .library import index_folder
from .store import Store, SCHEDULES, next_update, target


def import_legacy(store, media, subscriptions=None, history=None, favorites=None, avatars=None, config=None):
    media = Path(media).resolve()
    if not media.is_dir() or media == store.downloads:
        raise ValueError('旧媒体目录不可用或与新目录相同')
    store.legacy = media
    counts = {'posts':0, 'media':0, 'subscriptions':0, 'invalid_subscriptions':0}
    if config and Path(config).is_file() and not store.setting('x_proxy'):
        settings = json.loads(Path(config).read_text(encoding='utf-8'))
        extractor = settings.get('extractor',{})
        proxy = extractor.get('twitter',{}).get('proxy') or extractor.get('proxy') or settings.get('proxy')
        if proxy:
            store.set_setting('x_proxy',proxy)
    for source_folder in sorted(media.iterdir()):
        if not source_folder.is_dir() or source_folder.is_symlink():
            continue
        try:
            user = target(source_folder.name)['user']
        except ValueError:
            continue
        posts, files = index_folder(store, source_folder.name, legacy=True)
        counts['posts'] += posts
        counts['media'] += files
    if subscriptions:
        raw = json.loads(Path(subscriptions).read_text(encoding='utf-8'))
        rows = raw.get('subs',[]) if isinstance(raw,dict) else raw
        with store.connect() as db:
            for row in rows:
                try:
                    user = target(row.get('url') or row.get('user'))['user']
                    schedule = row.get('frequency', 'monthly')
                    if schedule not in SCHEDULES:
                        raise ValueError('旧订阅频率无法映射')
                except (ValueError, AttributeError):
                    counts['invalid_subscriptions'] += 1
                    continue
                store.add_blogger(user, db)
                db.execute('''INSERT INTO subscriptions(user,enabled,limit_count,schedule,next_run)
                              VALUES (?,?,0,?,?) ON CONFLICT(user) DO NOTHING''',
                           (user, bool(row.get('enabled',True)) and schedule != 'once', schedule, next_update(schedule)))
                counts['subscriptions'] += 1
    if favorites and Path(favorites).is_file():
        raw = json.loads(Path(favorites).read_text(encoding='utf-8'))
        users = raw.get('users',[]) if isinstance(raw,dict) else raw
        for value in users:
            try:
                user = target(value)['user']
            except ValueError:
                continue
            store.execute('UPDATE bloggers SET featured=1 WHERE user=?', (user,))
    if history and Path(history).is_file():
        with closing(sqlite3.connect(history)) as source, closing(sqlite3.connect(store.config / 'gallery-history.sqlite3')) as destination, destination:
            destination.execute('CREATE TABLE IF NOT EXISTS archive (entry TEXT PRIMARY KEY) WITHOUT ROWID')
            destination.executemany('INSERT OR IGNORE INTO archive VALUES (?)', source.execute('SELECT entry FROM archive'))
    if avatars and Path(avatars).is_dir():
        cached = {p.stem.lower():p for p in Path(avatars).iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.jpg','.jpeg','.png','.webp','.gif')}
        folder = store.config / 'avatars'
        folder.mkdir(exist_ok=True)
        for row in store.query("SELECT user,avatar_url FROM bloggers WHERE avatar_url!=''"):
            source = cached.get(row['user'])
            if source:
                name = row['user']+'-'+hashlib.sha256(row['avatar_url'].encode()).hexdigest()[:16]+'.image'
                destination = folder / name
                if not destination.exists():
                    shutil.copyfile(source,destination)
    with store.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    counts['totals'] = {t:store.query(f'SELECT count(*) n FROM {t}')[0]['n'] for t in ('posts','media','bloggers','subscriptions')}
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data',required=True)
    parser.add_argument('--media',required=True)
    parser.add_argument('--subscriptions')
    parser.add_argument('--history')
    parser.add_argument('--favorites')
    parser.add_argument('--avatars')
    parser.add_argument('--config')
    args = parser.parse_args()
    print(json.dumps(import_legacy(Store(args.data),args.media,args.subscriptions,args.history,args.favorites,args.avatars,args.config),ensure_ascii=False))


if __name__ == '__main__':
    main()
