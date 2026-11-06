"""Integrity checks for the user's actual document-KB batch and human ratings."""
import contextlib
import copy
import csv
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kb_eval as kb


class KBArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory=kb.ROOT/'reports/kb-run-01'
        self.report=json.loads((self.directory/'results.json').read_text())

    def test_original_hashes_csv_and_scoring_are_complete(self):
        manifest=json.loads((self.directory/'provenance.json').read_text())
        for name,info in manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.directory/name).read_bytes()).hexdigest(),info['sha256'])
        rows=self.report['rows']
        self.assertEqual(len(rows),20)
        self.assertEqual(len({r['workflow_run_id'] for r in rows}),20)
        self.assertTrue(all(r['status']=='ok' for r in rows))
        self.assertEqual(sum(r['source_hit'] is True for r in rows),15)
        self.assertEqual(sum(r['retrieval_hit'] is True for r in rows),14)
        self.assertEqual(sum(r['answer_correct'] is True for r in rows),13)
        self.assertEqual(sum(r['refusal_correct'] is True for r in rows),3)
        with (self.directory/'results.csv').open(encoding='utf-8-sig',newline='') as stream:
            csvrows=list(csv.DictReader(stream))
        self.assertEqual(len(csvrows),20)
        for c,r in zip(csvrows,rows):
            self.assertEqual(c['answer'],r['answer'])
            self.assertEqual(c['workflow_run_id'],r['workflow_run_id'])
            self.assertEqual(float(c['total_seconds']),r['total_seconds'])
            self.assertEqual(c['source_hit'],'' if r['source_hit'] is None else str(r['source_hit']))

    def test_evidence_recomputed_from_raw_response_and_metrics_match(self):
        for case,row in zip(kb.load_cases(),self.report['rows']):
            check=dict(**copy.deepcopy(case),expected_ids=[],fragments=[])
            kb.unpack(check,copy.deepcopy(row['dify_response']))
            for key in ('source_hit','retrieval_hit','answer','evidence_groups_covered','fragments','citation_valid'):
                self.assertEqual(check[key],row[key])
        stats=json.loads((self.directory/'metrics.json').read_text())
        self.assertEqual(stats['missed_evidence_ids'],['D11'])
        self.assertEqual(stats['wrong_answer_ids'],['D09','D11'])
        self.assertEqual(stats['correct_refusal_ids'],['D17','D19','D20'])
        self.assertEqual(stats['wrong_refusal_ids'],['D16','D18'])
        expected=sum(r['total_seconds'] for r in self.report['rows'])/20
        self.assertAlmostEqual(expected,stats['timings']['total_seconds']['mean'])

    def test_archived_report_does_not_modify_originals_and_review_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            directory=Path(temp)
            for name in ('results.json','results.csv','summary.md','provenance.json'):
                shutil.copyfile(self.directory/name,directory/name)
            before={n:(directory/n).read_bytes() for n in ('results.json','results.csv','summary.md')}
            with patch('kb_eval.de.api_key',side_effect=AssertionError('Offline operations need no key')):
                with patch('sys.argv',['kb_eval.py','report','--run-dir',str(directory)]):self.assertEqual(kb.main(),0)
                with patch('sys.argv',['kb_eval.py','review','--run-dir',str(directory)]):self.assertEqual(kb.main(),1)
            for name,value in before.items():self.assertEqual((directory/name).read_bytes(),value)
            self.assertIn('第四阶段',(directory/'report.html').read_text())


if __name__=='__main__':unittest.main()
