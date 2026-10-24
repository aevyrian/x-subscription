import tempfile
import unittest
import random
from collections import Counter
from datetime import datetime, timedelta
from unittest.mock import patch

from xarchive.store import LOCAL_TIME, SCHEDULES, Store, next_update, spread_updates


def moment(year=2028, month=1, day=31, hour=23, minute=10):
    return datetime(year, month, day, hour, minute, tzinfo=LOCAL_TIME).timestamp()


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def plan(self, user, schedule='weekly', due=0):
        with self.store.connect() as db:
            self.store.add_blogger(user, db)
            db.execute('INSERT INTO subscriptions(user,schedule,next_run) VALUES (?,?,?)', (user, schedule, due))

    def test_calendar_periods_preserve_dates_and_randomize_work_minutes(self):
        self.assertIn('quarterly', SCHEDULES)
        for schedule, date in [('daily', (2028, 2, 1)), ('three_days', (2028, 2, 3)),
                               ('weekly', (2028, 2, 7)), ('monthly', (2028, 2, 29)),
                               ('quarterly', (2028, 4, 30)), ('yearly', (2029, 1, 31))]:
            with self.subTest(schedule=schedule):
                result = datetime.fromtimestamp(next_update(schedule, moment()), LOCAL_TIME)
                self.assertEqual((result.year, result.month, result.day), date)
                self.assertTrue(8 <= result.hour < 18)
                self.assertEqual((result.second, result.microsecond), (0, 0))
        leap = datetime.fromtimestamp(next_update('yearly', moment(2028, 2, 29)), LOCAL_TIME)
        self.assertEqual((leap.year, leap.month, leap.day), (2029, 2, 28))
        self.assertEqual(next_update('once', moment()), 0)

    def test_quarterly_migration_runs_once_preserves_paused_and_spreads_dates(self):
        now = moment(2028, 1, 1, 12)
        for n in range(18):
            self.plan('plan' + str(n), 'once' if n == 0 else 'monthly')
        self.store.execute("UPDATE subscriptions SET enabled=0 WHERE user IN ('plan0','plan1')")
        self.store.execute("DELETE FROM settings WHERE key='quarterly_schedule_v1'")
        self.store.set_setting('default_schedule', 'monthly')
        with patch('xarchive.store.time.time', return_value=now):
            migrated = Store(self.tmp.name)
        rows = migrated.query('SELECT user,schedule,enabled,next_run FROM subscriptions ORDER BY user')
        self.assertEqual(migrated.setting('default_schedule'), 'quarterly')
        self.assertTrue(all(row['schedule'] == 'quarterly' for row in rows))
        self.assertEqual(sum(not row['enabled'] for row in rows), 2)
        dates = [datetime.fromtimestamp(row['next_run'], LOCAL_TIME) for row in rows]
        self.assertTrue(all(now < row['next_run'] < moment(2028, 4, 1, 0) for row in rows))
        self.assertTrue(all(8 <= date.hour < 18 for date in dates))
        self.assertGreater((max(dates) - min(dates)).days, 75)
        with patch('xarchive.store.time.time', return_value=now + 86400):
            reopened = Store(self.tmp.name)
        self.assertEqual(reopened.query('SELECT user,schedule,enabled,next_run FROM subscriptions ORDER BY user'), rows)

    def test_initial_spread_balances_daily_load(self):
        with patch('xarchive.store.random', random.Random(17)):
            dates = [datetime.fromtimestamp(due, LOCAL_TIME).date()
                     for _, due in spread_updates(list(range(407)), moment(2026, 10, 24), 91)]
        counts = Counter(dates)
        self.assertEqual(len(counts), 91)
        self.assertEqual(set(counts.values()), {4, 5})

    def test_schedule_enqueues_at_most_one_and_has_persistent_spacing(self):
        now = moment(2028, 1, 2, 9)
        for n in range(4):
            self.plan('plan' + str(n), due=now - 60)
        self.store.schedule(now)
        self.store.schedule(now + 1)
        self.assertEqual(len(self.store.query('SELECT * FROM tasks')), 1)
        self.assertEqual(self.store.query('SELECT source FROM tasks')[0]['source'], 'scheduled')
        reopened = Store(self.tmp.name)
        reopened.schedule(now + 2)
        self.assertEqual(len(self.store.query('SELECT * FROM tasks')), 1)
        reopened.schedule(now + 31 * 60)
        self.assertEqual(len(self.store.query('SELECT * FROM tasks')), 2)

    def test_automatic_claims_are_spaced_persistently_without_blocking_manual(self):
        now = moment(2028, 1, 2, 9)
        for user in ('automatic1', 'automatic2'):
            self.store.submit(user, source='scheduled')
        self.store.execute('UPDATE tasks SET next_run=?', (now - 60,))
        first = self.store.claim(now)
        self.store.finish(first)
        self.assertIsNone(self.store.claim(now + 1))
        manual, _ = self.store.submit('manual')
        self.assertEqual(self.store.claim(now + 2)['id'], manual['id'])
        self.assertIsNone(Store(self.tmp.name).claim(now + 3))
        self.assertIsNotNone(self.store.claim(now + 31 * 60))

    def test_daily_scheduler_spreads_missed_dates_without_restart(self):
        now = moment(2028, 1, 5, 9)
        for n in range(18):
            self.plan('plan' + str(n), due=moment(2028, 1, 4, 17))
        self.store.schedule(now)
        self.assertEqual(self.store.query('SELECT COUNT(*) n FROM tasks')[0]['n'], 0)
        dates = [datetime.fromtimestamp(row['next_run'], LOCAL_TIME).date() for row in self.store.query('SELECT next_run FROM subscriptions')]
        self.assertEqual(set(dates), {datetime.fromtimestamp(now, LOCAL_TIME).date() + timedelta(days=n) for n in (1, 2, 3)})

    def test_automatic_claim_obeys_window_but_manual_runs_anytime(self):
        automatic, _ = self.store.submit('automatic', source='scheduled')
        manual, _ = self.store.submit('manual', source='website')
        self.store.execute('UPDATE tasks SET next_run=0')
        night = moment(2028, 1, 2, 22)
        claimed = self.store.claim(night)
        self.assertEqual(claimed['id'], manual['id'])
        self.store.finish(claimed)
        self.assertIsNone(self.store.claim(night))
        self.assertIsNone(self.store.claim(moment(2028, 1, 3, 7, 59)))
        self.assertEqual(self.store.claim(moment(2028, 1, 3, 8, 0))['id'], automatic['id'])

    def test_automatic_retry_after_close_moves_to_work_window(self):
        now = moment(2028, 1, 2, 17, 55)
        self.store.submit('automatic', source='scheduled')
        self.store.execute('UPDATE tasks SET next_run=0')
        with patch('xarchive.store.time.time', return_value=now):
            task = self.store.claim()
            self.store.finish(task, error='fixture rate limit', retry_after=900)
        due = datetime.fromtimestamp(self.store.query('SELECT next_run FROM tasks')[0]['next_run'], LOCAL_TIME)
        self.assertEqual((due.year, due.month, due.day), (2028, 1, 3))
        self.assertTrue(8 <= due.hour < 18)

    def test_recovery_spreads_missed_dates_over_three_days_once(self):
        now = moment(2028, 1, 5, 9)
        for n in range(18):
            user = 'plan' + str(n)
            self.plan(user, due=moment(2028, 1, 1, 9))
            task, _ = self.store.submit(user, source='scheduled')
            self.store.execute('UPDATE tasks SET next_run=? WHERE id=?', (moment(2028, 1, 1, 9), task['id']))
        self.store.recover(now)
        plans = self.store.query('SELECT user,next_run FROM subscriptions ORDER BY user')
        tasks = self.store.query('SELECT id,next_run FROM tasks ORDER BY id')
        dates = [datetime.fromtimestamp(row['next_run'], LOCAL_TIME) for row in plans + tasks]
        self.assertEqual({date.date() for date in dates},
                         {datetime.fromtimestamp(now, LOCAL_TIME).date() + timedelta(days=n) for n in (1, 2, 3)})
        self.assertTrue(all(8 <= date.hour < 18 for date in dates))
        self.store.recover(now + 60)
        self.assertEqual(self.store.query('SELECT user,next_run FROM subscriptions ORDER BY user'), plans)
        self.assertEqual(self.store.query('SELECT id,next_run FROM tasks ORDER BY id'), tasks)


if __name__ == '__main__':
    unittest.main()
