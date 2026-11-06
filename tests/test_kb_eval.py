"""Document dataset and client tests; synthetic Dify replies are not model scores."""
import contextlib
import io
import json
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kb_eval as kb
import dify_eval as de
import qa_eval as qa

CASES=kb.load_cases()
BY_QUERY={c['query']:c for c in CASES}
KEY='SYNTHETIC-KB-KEY'


def response(case,wrong_section=False,wrong_query=False):
    source=(case['expected_sources'] or ['01-account-and-mfa.txt'])[0]
    content=(kb.DOCS/source).read_text()
    if wrong_section:content='这是一段主题相关但不包含标注证据的说明。'
    fragments=[dict(id='KB-1',title=source,content=content)]
    answer='协议测试桩文本 [KB-1]' if case['answerable'] else '资料不足，无法回答'
    parsed=dict(answer=answer,insufficient=not case['answerable'])
    outputs=dict(**parsed,raw_answer=json.dumps(parsed,ensure_ascii=False),
                 retrieval_body=json.dumps(dict(query='wrong' if wrong_query else case['query'],top_k=3,
                                                results=fragments),ensure_ascii=False))
    return dict(workflow_run_id='stub-'+case['id'],data=dict(id='stub-'+case['id'],workflow_id='stub-docs',
        status='succeeded',elapsed_time=.1,total_tokens=30,outputs=outputs))


class Stub(BaseHTTPRequestHandler):
    requests=[]
    fail=False
    def log_message(self,*_):pass
    def send(self,code,value):
        self.send_response(code);self.send_header('Content-Type','application/json');self.end_headers()
        self.wfile.write(json.dumps(value,ensure_ascii=False).encode())
    def do_GET(self):
        if self.path=='/v1/parameters':self.send(200,{'user_input_form':[{'text-input':{'variable':'query'}}]})
        else:self.send(200,{'name':'TEST-STUB-DOCS'})
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests.append(payload)
        case=BY_QUERY[payload['inputs']['query']]
        if self.fail and case['id']=='D01':self.send(500,{'error':'synthetic '+KEY});return
        self.send(200,response(case))


class EvidenceTests(unittest.TestCase):
    def row(self,index=0):
        return dict(**CASES[index],fragments=[],expected_ids=[])

    def test_20_cases_have_source_supported_gold_and_no_old_questions(self):
        self.assertEqual(len(CASES),20)
        self.assertEqual(sum(c['answerable'] for c in CASES),15)
        self.assertTrue(all(c['id'].startswith('D') for c in CASES))
        self.assertTrue(all(not c['expected_sources'] for c in CASES[15:]))

    def test_mac_update_kit_matches_current_scripts_and_data(self):
        from tools.build_kb_eval_kit import FILES
        with zipfile.ZipFile(qa.ROOT/'knowledge-base/kb-evaluation-kit.zip') as archive:
            self.assertEqual(set(archive.namelist()),set(FILES))
            for name in FILES:self.assertEqual(archive.read(name),(qa.ROOT/name).read_bytes())

    def test_correct_source_alone_is_not_evidence_hit(self):
        row=self.row()
        kb.unpack(row,response(CASES[0],wrong_section=True))
        self.assertTrue(row['source_hit'])
        self.assertFalse(row['retrieval_hit'])
        self.assertEqual(row['expected_ids'],['KB-1'])
        self.assertTrue(row['citation_valid'])
        self.assertIn('evidence_sha256',row['fragments'][0])

    def test_aliases_are_checked_against_actual_response(self):
        row=self.row()
        data=response(CASES[0])
        output=data['data']['outputs']
        output['answer']='不存在的引用 [KB-99]'
        output['raw_answer']=json.dumps(dict(answer=output['answer'],insufficient=False))
        kb.unpack(row,data)
        self.assertTrue(row['retrieval_hit'])
        self.assertFalse(row['citation_valid'])
        self.assertEqual(row['unknown_citations'],['KB-99'])

    def test_wrong_query_and_generic_source_names_are_errors(self):
        with self.assertRaises(qa.EvaluationError):kb.unpack(self.row(),response(CASES[0],wrong_query=True))
        value=response(CASES[0]);output=value['data']['outputs']
        retrieval=json.loads(output['retrieval_body']);retrieval['results'][0]['title']='知识库文档'
        output['retrieval_body']=json.dumps(retrieval)
        with self.assertRaises(qa.EvaluationError):kb.unpack(self.row(),value)


class KBHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Stub)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}/v1'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=5)
    def setUp(self):Stub.requests=[];Stub.fail=False

    def test_full_batch_saved_and_can_review_without_changing_old_baseline(self):
        baseline=(qa.ROOT/'reports/dify-run-01/results.json').read_bytes()
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            directory=Path(temp)/'run'
            args=SimpleNamespace(output_dir=directory,smoke=False)
            self.assertEqual(kb.run(args,de.Client(self.base,KEY,3)),0)
            report=json.loads((directory/'results.json').read_text())
            self.assertEqual(len(report['rows']),20)
            self.assertEqual(report['summary']['retrieval_hits'],15)
            self.assertTrue(all(r['answer_correct'] is None and r['refusal_correct'] is None for r in report['rows']))
            self.assertTrue(all(set(r['inputs'])=={'query'} for r in Stub.requests))
            self.assertIn('第四阶段',(directory/'summary.md').read_text())
            self.assertIn('第四阶段',(directory/'report.html').read_text())
            for p in directory.iterdir():self.assertNotIn(KEY,p.read_text(encoding='utf-8-sig'))
            with patch('sys.argv',['kb_eval.py','review','--run-dir',str(directory)]), \
                 patch('builtins.input',side_effect=['n','测试桩不是语义评测','q']):
                self.assertEqual(kb.main(),0)
            reviewed=json.loads((directory/'results.json').read_text())
            self.assertFalse(reviewed['rows'][0]['answer_correct'])
            before=(directory/'results.json').read_bytes()
            with patch('sys.argv',['kb_eval.py','report','--run-dir',str(directory)]):self.assertEqual(kb.main(),0)
            self.assertEqual(before,(directory/'results.json').read_bytes())
        self.assertEqual(baseline,(qa.ROOT/'reports/dify-run-01/results.json').read_bytes())

    def test_smoke_failure_is_saved_and_key_redacted(self):
        Stub.fail=True
        try:
            with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
                directory=Path(temp)/'run'
                self.assertEqual(kb.run(SimpleNamespace(output_dir=directory,smoke=True),de.Client(self.base,KEY,3)),1)
                report=json.loads((directory/'results.json').read_text())
                self.assertEqual(report['summary']['errors'],['D01'])
                self.assertNotIn(KEY,(directory/'results.json').read_text())
                self.assertEqual(len(Stub.requests),1)
        finally:Stub.fail=False


if __name__=='__main__':unittest.main()
