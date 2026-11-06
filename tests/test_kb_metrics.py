"""Metric math, strict-gold completeness, immutable audit and corpus export checks."""
import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

import kb_metrics as km
import kb_eval as kb
import qa_eval as qa

RUN=qa.ROOT/'reports/kb-run-01'
REVIEWS=qa.ROOT/'evaluation/reviews/kb-run-01'


class MetricsTests(unittest.TestCase):
    def test_existing_run_audit_and_source_immutability(self):
        before=(RUN/'results.json').read_bytes()
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            out=Path(temp)/'audit'
            data=km.analyze(RUN,REVIEWS,out)
            self.assertAlmostEqual(data['retrieval']['precision_at_3']['value'],.4)
            self.assertAlmostEqual(data['retrieval']['hit_at_3']['value'],14/15)
            self.assertAlmostEqual(data['retrieval']['reciprocal_rank_at_3']['value'],.9)
            self.assertIsNone(data['retrieval']['recall_at_3']['value'])
            self.assertEqual(data['generation_audit']['supported_claim_count'],36)
            self.assertEqual(data['generation_audit']['factual_claim_count'],37)
            self.assertAlmostEqual(data['timing']['p95_nearest_rank'],10.331544)
            self.assertTrue((out/'report.html').exists())
        self.assertEqual(before,(RUN/'results.json').read_bytes())

    def fixture(self,root):
        run=json.loads((RUN/'results.json').read_text())
        source='01-account-and-mfa.txt'
        for row in run['rows']:
            row['fragments']=[dict(id=f'KB-{i}',source_document=source,title=source,content=body)
                              for i,body in enumerate(('a','b','c'),1)]
        run_dir=root/'run';run_dir.mkdir();qa.write_json(run_dir/'results.json',run)
        corpus=dict(complete=True,segments=[dict(stable_id=name,source_document=source,content=name)
                                          for name in ('a','b','c','d')])
        cp=root/'corpus.json';qa.write_json(cp,corpus)
        qd=root/'gold';qd.mkdir()
        qa.write_json(qd/'review-manifest.json',dict(corpus_sha256=hashlib.sha256(cp.read_bytes()).hexdigest(),
            questions_sha256=hashlib.sha256(kb.QUESTIONS.read_bytes()).hexdigest()))
        pairs=[]
        for row in run['rows'][:15]:
            for s in corpus['segments']:
                positive=s['stable_id']=='a' or (row['id']=='D01' and s['stable_id']=='d')
                pairs.append(dict(question_id=row['id'],stable_id=s['stable_id'],source_document=source,
                    content=s['content'],relevance=2 if positive else 0,reviewer='synthetic-unit-test',reason='metric fixture'))
        km.write_csv(qd/'qrels.csv',pairs,list(pairs[0]))
        return run_dir,cp,qd,run,pairs

    def test_strict_recall_counts_unretrieved_gold_and_excludes_no_answer(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);rd,cp,qd,_,_=self.fixture(root)
            data=km.analyze(rd,None,root/'audit',cp,qd)
            self.assertAlmostEqual(data['per_question'][0]['recall_at_3'],.5)
            self.assertAlmostEqual(data['retrieval']['recall_at_3']['value'],14.5/15)
            self.assertAlmostEqual(data['retrieval']['precision_at_3']['value'],1/3)
            self.assertEqual(data['retrieval']['recall_at_3']['evaluated'],15)
            self.assertTrue(all(p['recall_at_3'] is None for p in data['per_question'][15:]))

    def test_partial_blank_stale_or_ambiguous_gold_rejected(self):
        for mode in ('missing_pair','blank_grade','stale_corpus','ambiguous'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);rd,cp,qd,run,pairs=self.fixture(root)
                if mode=='missing_pair':pairs.pop()
                if mode=='blank_grade':pairs[0]['relevance']=''
                if mode in ('missing_pair','blank_grade'):km.write_csv(qd/'qrels.csv',pairs,list(pairs[0]))
                if mode=='stale_corpus':cp.write_text(cp.read_text()+'\n')
                if mode=='ambiguous':
                    data=json.loads(cp.read_text());data['segments'][1]['content']='a';qa.write_json(cp,data)
                    m=json.loads((qd/'review-manifest.json').read_text());m['corpus_sha256']=hashlib.sha256(cp.read_bytes()).hexdigest();qa.write_json(qd/'review-manifest.json',m)
                    for pair in pairs:
                        if pair['stable_id']=='b':pair['content']='a'
                    km.write_csv(qd/'qrels.csv',pairs,list(pairs[0]))
                with self.assertRaises(qa.EvaluationError):km.strict_qrels(cp,qd,run['rows'])

    def test_review_tampering_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);import shutil
            shutil.copytree(REVIEWS,root/'reviews')
            p=root/'reviews/retrieval.csv';p.write_bytes(p.read_bytes()+b'\n')
            with self.assertRaises(qa.EvaluationError):km.analyze(RUN,root/'reviews',root/'audit')


class FakeDatasetAPI:
    def __init__(self,not_ready=False):self.calls=[];self.not_ready=not_ready
    def request(self,path,payload=None):
        assert payload is None
        self.calls.append(path)
        parsed=urlsplit(path);page=int(parse_qs(parsed.query).get('page',['1'])[0])
        docs=[dict(id=f'doc{i}',name=p.name,indexing_status='completed',enabled=True)
              for i,p in enumerate(sorted(kb.DOCS.glob('*.txt')),1)]
        if self.not_ready:docs[0]['indexing_status']='indexing'
        if parsed.path.endswith('/documents'):
            return dict(data=docs[:2] if page==1 else docs[2:],has_more=page==1)
        did=parsed.path.split('/')[-2]
        return dict(data=[dict(id='segment-'+did,content='synthetic paragraph',enabled=True,status='completed')],has_more=False)


class ExportTests(unittest.TestCase):
    def test_paginated_export_has_permanent_ids_and_blank_gold_template(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);client=FakeDatasetAPI()
            corpus=km.export_corpus(client,['dataset1'],root/'export')
            self.assertEqual(len(corpus['documents']),3)
            self.assertEqual(len(corpus['segments']),3)
            self.assertTrue(all(s['stable_id'].startswith('dify:dataset1:doc') for s in corpus['segments']))
            self.assertTrue(any('page=2' in p for p in client.calls))
            km.prepare(root/'export/segments.json',root/'gold')
            pairs=km.read_csv(root/'gold/qrels.csv')
            self.assertEqual(len(pairs),45)
            self.assertTrue(all(p['relevance']=='' and p['reviewer']=='' for p in pairs))

    def test_indexing_in_progress_does_not_get_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'export'
            with self.assertRaises(qa.EvaluationError):km.export_corpus(FakeDatasetAPI(True),['dataset1'],out)
            self.assertFalse((out/'segments.json').exists())


if __name__=='__main__':unittest.main()
