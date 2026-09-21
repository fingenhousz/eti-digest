import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'test')
os.environ.setdefault('TELEGRAM_CHAT_ID', 'test')
os.environ.setdefault('ANTHROPIC_API_KEY', 'test')
import eti_digest as digest
import send_reminders as reminders
from alert_policy import company_keys, excluded_names, fresh_article_date


class PolicyTests(unittest.TestCase):
    def test_company_variants(self):
        for left, right in [('Groupe Okaïdi SAS', 'OKAIDI'),
                            ('FICA-HPCI', 'FICA HPCI'),
                            ('NanoXplore (PME défense/spatial)', 'NanoXplore'),
                            ('Groupe Positive / Sigilium', 'Sigilium')]:
            self.assertTrue(company_keys(left) & company_keys(right))
        self.assertFalse(company_keys('ABC') & company_keys('ABC Industrie'))

    def test_exclusions_and_status(self):
        now = datetime.now(timezone.utc)
        def entry(days, status='pending'):
            return {'name': str(days) + status, 'date': (now-timedelta(days=days)).isoformat(), 'status': status}
        history = {str(i): e for i, e in enumerate([entry(89), entry(91), entry(200, 'pass'), entry(200, 'interested')])}
        self.assertEqual(excluded_names(history, now), ['89pending', '200pass', '200interested'])

    def test_freshness(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(fresh_article_date(format_datetime(now-timedelta(hours=71)), now))
        for value in [None, '', 'unknown', format_datetime(now-timedelta(hours=73)), format_datetime(now+timedelta(days=1))]:
            self.assertIsNone(fresh_article_date(value, now))

    def test_header_with_and_without_emoji(self):
        for header in ['*🏭 Fibre Excellence* — Paris', '*Fibre Excellence* — Paris', '**🏭 Fibre Excellence** — Paris']:
            self.assertEqual(digest.extract_company_names(header), ['Fibre Excellence'])
        self.assertEqual(digest.extract_company_names('Contexte : *nouvelle acquisition*'), [])

    def test_history_not_pruned_and_corruption_stops_run(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(digest, 'SENT_HISTORY_FILE', str(Path(folder)/'history.json')):
            old = {'old': {'name': 'Old', 'date': '2020-01-01', 'status': 'interested', 'reminded_at': '2020-02-01'}}
            digest.save_sent_history(old)
            self.assertEqual(digest.load_sent_history(), {**old, 'old': {**old['old'], 'sector': None}})
            Path(digest.SENT_HISTORY_FILE).write_text('{broken', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                digest.load_sent_history()

    def test_rss_old_undated_and_duplicate_articles(self):
        fresh = {'title': 'Acquisition entreprise', 'published': format_datetime(datetime.now(timezone.utc)-timedelta(hours=1))}
        old = {**fresh, 'published': 'Mon, 01 Jan 2024 10:00:00 GMT'}
        with patch.object(digest.feedparser, 'parse', return_value=SimpleNamespace(entries=[fresh, old, {'title': 'rachat'}])):
            self.assertEqual(len(digest.fetch_rss_news()), 1)

    def test_delivery_filters_and_checkpoints(self):
        blocks = ['*🏭 Groupe Okaidi* — Paris', '*🏭 New Company* — Paris', '*🏭 New-Company SAS* — Paris', '*🏭 Failed Company* — Paris']
        with tempfile.TemporaryDirectory() as folder, patch.object(digest, 'SENT_HISTORY_FILE', str(Path(folder)/'history.json')):
            digest.save_sent_history({'old': {'name': 'Okaïdi', 'date': datetime.now(timezone.utc).date().isoformat(), 'status': 'pending'}})
            def send(message, **kwargs):
                if 'Failed Company' in message:
                    # First delivery already checkpointed before the next attempt.
                    self.assertIn(digest.company_id('New Company'), digest.load_sent_history())
                    return False
                return True
            with patch.object(digest, 'fetch_bodacc_events', return_value=[]), patch.object(digest, 'fetch_rss_news', return_value=[{}]), patch.object(digest, 'filter_with_size_data', return_value=[]), patch.object(digest, 'build_digest', return_value='---SPLIT---'.join(blocks)), patch.object(digest.time, 'sleep'), patch.object(digest, 'send_telegram', side_effect=send) as sender:
                with self.assertRaises(SystemExit):
                    digest.main()
                self.assertEqual(sender.call_count, 3)  # Header + two unique new companies.
            history = digest.load_sent_history()
            self.assertIn(digest.company_id('New Company'), history)
            self.assertNotIn(digest.company_id('Failed Company'), history)

    def test_reminder_only_once(self):
        entry = {'name': 'Company', 'status': 'interested', 'interested_at': '2020-01-01T00:00:00+00:00', 'reminded_at': '2020-01-10T00:00:00+00:00'}
        with patch.object(reminders, 'load_sent_history', return_value={'x': entry}), patch.object(reminders, 'send_telegram') as sender:
            reminders.main()
            sender.assert_not_called()


if __name__ == '__main__':
    unittest.main()
