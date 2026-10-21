import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nba_bot.core import identity
from nba_bot.injuries import PARSER_VERSION, PDFDependencyError, extract, pdf_pages, positioned_text
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

    def test_new_parser_preserves_previous_extraction_instead_of_reusing_it(self):
        old={'parser_version':'pypdf-6.21.1-layout-v1','pages':[{'page':1,'text':'Earlier reverse-order extraction'}]}
        store=Store(self.path)
        with store.db:
            store.db.execute('INSERT INTO injury_texts VALUES (?,?)',
                             (identity([self.digest,old['parser_version']]),json.dumps(old)))
        store.close()
        with patch('nba_bot.injuries.pdf_pages',return_value=[{'page':1,'text':'Corrected source order'}]):
            self.assertEqual(extract(self.path)['parser_version'],PARSER_VERSION)
        store=Store(self.path)
        try:
            records=store.db.execute('SELECT payload FROM injury_texts ORDER BY rowid').fetchall()
            self.assertEqual(len(records),2)
            self.assertEqual(json.loads(records[0][0]),old)
            self.assertEqual(store.report(False)['latest_injury_text']['parser_version'],PARSER_VERSION)
        finally:
            store.close()


class PDFReadingOrderTests(unittest.TestCase):
    def test_baseline_jitter_and_scrambled_fragment_order_preserve_columns(self):
        fragments=[(120,100,'Reason'),(80,100.3,'Out'),(0,130,'Header'),
                   (40,100,'Player'),(120,88,'Continuation'),(0,100,'Team'),(0,20,'Footer')]
        lines=positioned_text(fragments).splitlines()
        self.assertEqual(lines[0],'Header')
        self.assertLess(lines[1].index('Team'),lines[1].index('Player'))
        self.assertLess(lines[1].index('Player'),lines[1].index('Out'))
        self.assertLess(lines[1].index('Out'),lines[1].index('Reason'))
        self.assertEqual(lines[2].strip(),'Continuation')
        self.assertEqual(lines[-1],'Footer')

    def test_invalid_coordinates_are_rejected(self):
        for coordinate in (float('nan'),float('inf'),1e9):
            with self.subTest(coordinate=coordinate), self.assertRaises(ValueError):
                positioned_text([(coordinate,100,'Unsupported')])

    def test_real_pdf_with_flipped_coordinates_and_negative_font_size(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        except ImportError:
            self.skipTest('Pinned PDF reader is not installed; install requirements.txt')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'report.pdf'
            writer=PdfWriter()
            for flipped in (True,False):
                page=writer.add_blank_page(width=600,height=800)
                font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
                page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
                stream=DecodedStreamObject()
                # Deliberately write footer before header: stream order also
                # cannot determine the visual reading order.
                if flipped:
                    content=b'1 0 0 -1 0 800 cm BT /F1 -10 Tf 1 0 0 1 20 750 Tm (Footer) Tj 1 0 0 1 20 50 Tm (Header) Tj 1 0 0 1 20 100 Tm (Player) Tj 1 0 0 1 250 100 Tm (Out) Tj ET'
                else:
                    content=b'BT /F1 10 Tf 1 0 0 1 20 50 Tm (Footer) Tj 1 0 0 1 20 750 Tm (Header) Tj 1 0 0 1 20 700 Tm (Player) Tj 1 0 0 1 250 700 Tm (Out) Tj ET'
                stream.set_data(content)
                page[NameObject('/Contents')]=writer._add_object(stream)
            writer.write(path)
            pages=pdf_pages(path)
        self.assertEqual(len(pages),2)
        for page in pages:
            lines=page['text'].splitlines()
            self.assertEqual(lines[0].strip(),'Header')
            self.assertEqual(lines[-1].strip(),'Footer')
            self.assertLess(lines[1].index('Player'),lines[1].index('Out'))
