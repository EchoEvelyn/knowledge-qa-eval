"""Check immutable uploaded evidence and derived cross-stage comparisons."""
import contextlib
import csv
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dify_eval as de
import qa_eval as qa


class DifyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory=qa.ROOT/'reports/dify-run-01'
        self.report=json.loads((self.directory/'results.json').read_text())

    def test_original_files_hashes_csv_and_scores(self):
        manifest=json.loads((self.directory/'provenance.json').read_text())
        for name,info in manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.directory/name).read_bytes()).hexdigest(),info['sha256'])
        rows=self.report['rows']
        self.assertEqual(len(rows),20)
        self.assertEqual(len({r['workflow_run_id'] for r in rows}),20)
        self.assertTrue(all(r['status']=='ok' and r['workflow_status']=='succeeded' for r in rows))
        self.assertEqual(sum(r['retrieval_hit'] is True for r in rows),14)
        self.assertEqual(sum(r['answer_correct'] is True for r in rows),13)
        self.assertEqual(sum(r['refusal_correct'] is True for r in rows),1)
        with (self.directory/'results.csv').open(encoding='utf-8-sig',newline='') as stream:
            records=list(csv.DictReader(stream))
        for c,r in zip(records,rows):
            self.assertEqual(c['answer'],r['answer'])
            self.assertEqual(c['workflow_run_id'],r['workflow_run_id'])
            self.assertEqual(float(c['total_seconds']),r['total_seconds'])
            self.assertEqual(c['retrieval_seconds'],'')
            self.assertEqual(c['generation_seconds'],'')

    def test_comparison_matches_both_raw_runs(self):
        local=json.loads((qa.ROOT/'reports/mac-run-02/results.json').read_text())
        comparison=json.loads((self.directory/'comparison.json').read_text())
        for first,second in zip(local['rows'],self.report['rows']):
            for key in ('query','fragments','answer','answer_correct','refusal_correct'):
                self.assertEqual(first[key],second[key])
        self.assertEqual(comparison['answer_same'],20)
        expected=sum(r['total_seconds'] for r in self.report['rows'])/20
        self.assertAlmostEqual(expected,comparison['timings']['dify_client']['mean'])
        self.assertAlmostEqual(expected-sum(r['total_seconds'] for r in local['rows'])/20,
                               comparison['client_mean_difference_seconds'])

    def test_archive_report_is_offline_read_only_and_review_rejected(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            directory=Path(temp)
            for name in ('results.json','results.csv','summary.md','provenance.json'):
                shutil.copyfile(self.directory/name,directory/name)
            before={name:(directory/name).read_bytes() for name in ('results.json','results.csv','summary.md')}
            with patch('dify_eval.api_key',side_effect=AssertionError('Must not ask for API key')):
                with patch('sys.argv',['dify_eval.py','report','--run-dir',str(directory)]):
                    self.assertEqual(de.main(),0)
                with patch('sys.argv',['dify_eval.py','review','--run-dir',str(directory)]):
                    self.assertEqual(de.main(),1)
            self.assertTrue((directory/'report.html').exists())
            for name,content in before.items():
                self.assertEqual((directory/name).read_bytes(),content)


if __name__=='__main__':
    unittest.main()
