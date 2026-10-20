import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from nba_bot.core import now
from nba_bot.providers import mock_candidates
from nba_bot.settings import book_preferences
from nba_bot.store import Store


class SettingsTests(unittest.TestCase):
    def test_preferences_are_user_declared_and_normalized(self):
        with patch.dict('os.environ', {'AVAILABLE_BOOKS':'FanDuel, fanduel', 'USER_JURISDICTION':'us-va'}):
            settings=book_preferences()
            self.assertEqual(settings['user_listed_books'],['fanduel'])
            self.assertEqual(settings['jurisdiction'],'US-VA')
            self.assertFalse(settings['eligibility_verified'])

    def test_malformed_book_configuration_rejected(self):
        with patch.dict('os.environ', {'AVAILABLE_BOOKS':'https://example.test/book'}):
            with self.assertRaises(ValueError):
                book_preferences()

    def test_fanduel_vs_references_and_missing_book(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'AVAILABLE_BOOKS':'fanduel', 'USER_JURISDICTION':'US-VA'}):
            store=Store(Path(folder)/'books.sqlite')
            try:
                q=mock_candidates(now())[0][0]
                store.collect([replace(q,simulated=False,book='fanduel',book_name='FanDuel'),
                               replace(q,simulated=False,book='reference',book_name='Reference Book')])
                report=store.report(False)
                self.assertEqual(report['book_coverage']['user_listed_quotes'],1)
                self.assertEqual(report['book_coverage']['reference_quotes'],1)
                self.assertEqual(report['book_coverage']['listed_books_missing_from_snapshot'],[])
                self.assertEqual({q['book']:q['book_role'] for q in report['quotes']},
                                 {'fanduel':'User-listed sportsbook','reference':'Market reference'})
                self.assertEqual(report['summary']['selections'],0)
                store.collect([],simulated=False)
                self.assertEqual(store.report(False)['book_coverage']['listed_books_missing_from_snapshot'],['fanduel'])
            finally:
                store.close()

    def test_mock_books_never_become_user_available(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'AVAILABLE_BOOKS':'demo_book_a'}):
            store=Store(Path(folder)/'books.sqlite')
            try:
                store.collect([mock_candidates(now())[0][0]])
                row=store.report(True)['quotes'][0]
                self.assertFalse(row['user_listed_book'])
                self.assertEqual(row['book_role'],'Simulated fixture')
            finally:
                store.close()
