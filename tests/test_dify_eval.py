"""Real HTTP client tests against a synthetic Dify API; no real model scores."""
import contextlib
import csv
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dify_eval as de
import qa_eval as qa

KEY='SYNTHETIC-PRIVATE-KEY'
BASELINE=json.loads((qa.ROOT/'reports/mac-run-02/results.json').read_text())
CASES={r['query']:r for r in BASELINE['rows']}


class APIStub(BaseHTTPRequestHandler):
    mode='ok'
    requests=[]
    leaked=False

    def log_message(self,*_):
        pass

    def send(self,status,value):
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        try:
            self.wfile.write(json.dumps(value,ensure_ascii=False).encode())
        except (BrokenPipeError,ConnectionResetError):
            pass

    def do_GET(self):
        if self.path=='/leak':
            type(self).leaked=True
            self.send(200,{})
            return
        if self.mode=='redirect':
            self.send_response(302)
            self.send_header('Location','/leak')
            self.end_headers()
        elif self.mode=='unauthorized' or self.headers.get('Authorization')!='Bearer '+KEY:
            self.send(401,{'message':'invalid '+KEY})
        elif self.path=='/v1/parameters':
            self.send(200,{'user_input_form':[{'text-input':{'variable':'query','required':True}}]})
        elif self.path=='/v1/info':
            self.send(200,{'name':'TEST-STUB-NOT-DIFY','api_key':KEY})
        else:
            self.send(404,{})

    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests.append(payload)
        case=CASES[payload['inputs']['query']]
        if self.mode=='timeout':
            time.sleep(.15)
        if self.mode=='workflow_failed' and case['id']=='Q01':
            self.send(200,{'workflow_run_id':'failed-Q01','data':{'status':'failed','error':'synthetic node failure'}})
            return
        if self.mode=='auth_lost':
            self.send(403,{'message':'synthetic permission failure'})
            return
        fragments=case['fragments']
        answer=fragments[0]['content']+' ['+fragments[0]['id']+']' if fragments else '资料不足，无法回答'
        raw=json.dumps(dict(answer=answer,insufficient=not bool(fragments)),ensure_ascii=False)
        retrieval=dict(query=case['query'],top_k=3,results=fragments)
        if self.mode=='wrong_query':
            retrieval['query']='固定错误问题'
        outputs=dict(answer=answer,insufficient=not bool(fragments),raw_answer=raw,
                     retrieval_body=json.dumps(retrieval,ensure_ascii=False))
        if self.mode=='missing_output':
            outputs.pop('raw_answer')
        self.send(200,dict(workflow_run_id='run-'+case['id'],data=dict(id='run-'+case['id'],
            workflow_id='synthetic-workflow',status='succeeded',outputs=outputs,elapsed_time=.2,total_tokens=50)))


class DifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),APIStub)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}/v1'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=5)

    def setUp(self):
        APIStub.mode='ok';APIStub.requests=[];APIStub.leaked=False

    def args(self,directory,smoke=False):
        return SimpleNamespace(output_dir=directory,smoke=smoke)

    def test_all_20_and_manual_review_share_original_scoring(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory=Path(temp)/'run'
            self.assertEqual(de.run(self.args(directory),de.Client(self.base,KEY,3)),0)
            report=json.loads((directory/'results.json').read_text())
            self.assertEqual(len(report['rows']),20)
            self.assertEqual(report['summary']['retrieval_hits'],14)
            self.assertEqual(report['summary']['unanswerable_with_fragments'],['Q16','Q17','Q18'])
            self.assertTrue(all(r['answer_correct'] is None and r['refusal_correct'] is None for r in report['rows']))
            self.assertTrue(all(r['retrieval_seconds'] is None and r['generation_seconds'] is None for r in report['rows']))
            self.assertEqual(report['rows'][0]['workflow_run_id'],'run-Q01')
            for request in APIStub.requests:
                self.assertEqual(set(request),{'inputs','response_mode','user'})
                self.assertEqual(set(request['inputs']),{'query'})
                self.assertEqual(request['response_mode'],'blocking')
            for path in directory.iterdir():
                self.assertNotIn(KEY,path.read_text(encoding='utf-8-sig'))
            with patch('sys.argv',['dify_eval.py','review','--run-dir',str(directory)]), \
                    patch('builtins.input',side_effect=['n','synthetic fixture is not a semantic evaluation','q']):
                self.assertEqual(de.main(),0)
            report=json.loads((directory/'results.json').read_text())
            self.assertFalse(report['rows'][0]['answer_correct'])
            self.assertIn('第三阶段',(directory/'summary.md').read_text())
            with (directory/'results.csv').open(encoding='utf-8-sig',newline='') as stream:
                self.assertEqual(next(csv.DictReader(stream))['workflow_run_id'],'run-Q01')

    def test_preflight_401_saved_without_secret_and_no_generation(self):
        APIStub.mode='unauthorized'
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            directory=Path(temp)/'run'
            with self.assertRaises(de.DifyHTTPError) as raised:
                de.preflight(de.Client(self.base,KEY,3))
            self.assertEqual(raised.exception.status,401)
            with self.assertRaises(qa.EvaluationError):
                de.run(self.args(directory),de.Client(self.base,KEY,3))
            report=json.loads((directory/'results.json').read_text())
            self.assertEqual(report['summary']['run_state'],'preflight_failed')
            self.assertNotIn(KEY,(directory/'results.json').read_text())
            self.assertEqual(APIStub.requests,[])

    def test_workflow_http200_failure_is_not_success_and_continues(self):
        APIStub.mode='workflow_failed'
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            directory=Path(temp)/'run'
            self.assertEqual(de.run(self.args(directory),de.Client(self.base,KEY,3)),1)
            report=json.loads((directory/'results.json').read_text())
            self.assertEqual(report['summary']['errors'],['Q01'])
            self.assertEqual(len(report['rows']),20)
            self.assertEqual(report['rows'][0]['workflow_run_id'],'failed-Q01')

    def test_schema_mismatch_or_wrong_query_saved_as_error(self):
        for mode in ('missing_output','wrong_query'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
                APIStub.mode=mode
                directory=Path(temp)/'run'
                self.assertEqual(de.run(self.args(directory,True),de.Client(self.base,KEY,3)),1)
                row=json.loads((directory/'results.json').read_text())['rows'][0]
                self.assertIn('dify_response',row)
                self.assertEqual(row['status'],'error')

    def test_auth_lost_stops_batch_and_timeout_is_not_retried(self):
        for mode,timeout in [('auth_lost',3),('timeout',.05)]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
                APIStub.mode=mode;APIStub.requests=[]
                directory=Path(temp)/'run'
                self.assertEqual(de.run(self.args(directory,True),de.Client(self.base,KEY,timeout)),1)
                report=json.loads((directory/'results.json').read_text())
                self.assertEqual(len(APIStub.requests),1)
                self.assertEqual(len(report['rows']),1)
                if mode=='auth_lost':self.assertIn('aborted_reason',report)
                else:self.assertIn('timed out',report['rows'][0]['error'])

    def test_redirect_rejected_and_nonempty_query_required(self):
        APIStub.mode='redirect'
        with self.assertRaises(de.DifyHTTPError) as raised:
            de.preflight(de.Client(self.base,KEY,3))
        self.assertEqual(raised.exception.status,302)
        self.assertFalse(APIStub.leaked)
        with self.assertRaises(qa.EvaluationError):
            de.Client('http://user:password@localhost/v1',KEY,3)


if __name__=='__main__':
    unittest.main()
