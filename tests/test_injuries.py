import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nba_bot.core import identity
from nba_bot.injuries import PDFDependencyError, extract
from nba_bot.store import Store


class InjuryTextTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'paper.sqlite'
        self.raw = b'%PDF-1.4\nsynthetic boundary fixture'
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.url = 'https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-20_07_30PM.pdf'
        self.source = self.path.parent / 'source-artifacts' / (self.digest + '.source')
        self.source.parent.mkdir()
        self.source.write_bytes(self.raw)
        store = Store(self.path)
        with store.db:
            store.db.execute('INSERT INTO source_check_attempts VALUES (?,?)', (1, '2026-10-20T23:37:01+00:00'))
            store.db.execute('INSERT INTO source_artifacts VALUES (?,?,?,?)',
                             (identity([self.url, self.digest]), self.url, self.digest, '2026-10-20T23:37:02+00:00'))
            report = {'sources': {'official_injury_report': {'status': 'pdf_archived',
                      'source_url': self.url, 'filename_timestamp_label': '2026-10-20 07:30PM',
                      'publication_time_utc': None, 'artifacts': [{'url': self.url, 'sha256': self.digest}]}}}
            store.db.execute('INSERT INTO source_checks VALUES (?,?)', (1, json.dumps(report)))
        store.close()

    def test_verified_text_is_cached_immutable_and_separate_from_mock(self):
        with patch('nba_bot.injuries.pdf_pages', return_value=[{'page': 1, 'text': 'Synthetic report source text'}]) as reader:
            first = extract(self.path)
            second = extract(self.path)
            reader.assert_called_once()
        self.assertEqual(first['status'], 'text_extracted')
        self.assertFalse(first['player_claims_mapped'])
        self.assertIsNone(first['publication_time_utc'])
        self.assertTrue(second['cached'])
        self.assertEqual(first['processed_at'], second['processed_at'])
        store = Store(self.path)
        try:
            self.assertIsNotNone(store.report(False)['latest_injury_text'])
            self.assertIsNone(store.report(True)['latest_injury_text'])
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM collection_attempts').fetchone()[0], 0)
            with self.assertRaises(sqlite3.DatabaseError):
                store.db.execute('DELETE FROM injury_texts')
        finally:
            store.close()

    def test_changed_bytes_are_rejected_before_pdf_reader(self):
        self.source.write_bytes(self.raw + b'changed')
        with patch('nba_bot.injuries.pdf_pages') as reader:
            self.assertEqual(extract(self.path)['status'], 'error')
            reader.assert_not_called()

    def test_redirected_artifact_is_rejected(self):
        other = self.path.parent / 'other.pdf'
        other.write_bytes(self.raw)
        self.source.unlink()
        self.source.symlink_to(other)
        with patch('nba_bot.injuries.pdf_pages') as reader:
            self.assertEqual(extract(self.path)['status'], 'error')
            reader.assert_not_called()

    def test_missing_dependency_is_actionable_without_invented_text(self):
        with patch('nba_bot.injuries.pdf_pages', side_effect=PDFDependencyError('Install pinned reader')):
            result = extract(self.path)
        self.assertEqual(result['status'], 'dependency_missing')
        self.assertIn('--require-hashes', result['build_command'])
        self.assertNotIn('pages', result)

    def test_new_failed_check_does_not_silently_extract_older_report(self):
        store = Store(self.path)
        with store.db:
            store.db.execute('INSERT INTO source_check_attempts VALUES (?,?)', (2, '2026-10-20T23:42:01+00:00'))
            store.db.execute('INSERT INTO source_checks VALUES (?,?)',
                             (2, json.dumps({'sources': {'official_injury_report': {'status': 'error'}}})))
        store.close()
        with patch('nba_bot.injuries.pdf_pages') as reader:
            self.assertEqual(extract(self.path)['status'], 'not_ready')
            reader.assert_not_called()
