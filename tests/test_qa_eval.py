"""Synthetic Ollama responses test the client protocol, never model quality."""
import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import qa_eval as qa


class ParsingTests(unittest.TestCase):
    def test_nonexistent_citation_is_flagged(self):
        value = qa.parse_answer({'done': True, 'message': {'content': json.dumps(
            {'answer': '说明 [IT-999]', 'insufficient': False})}}, [{'id': 'IT-001'}])
        self.assertFalse(value['citation_valid'])
        self.assertEqual(value['unknown_citations'], ['IT-999'])

    def test_unfinished_or_invalid_response_is_not_a_success(self):
        for raw in ([], {'message': []}, {'done': False, 'message': {'content': '{}'}},
                    {'done': True, 'done_reason': 'length', 'message': {'content': '{}'}},
                    {'done': True, 'message': {'content': 'not json'}},
                    {'done': True, 'message': {'content': '{"answer":"x","insufficient":"false"}'}},
                    {'done': True, 'message': {'content': ''}}):
            with self.subTest(raw=raw), self.assertRaises(qa.EvaluationError):
                qa.parse_answer(raw, [])

    def test_echoed_input_is_rejected_even_when_valid_json(self):
        echo = {'question': '忘记密码', 'fragments': [{'id': 'IT-001', 'content': '重置'}]}
        with self.assertRaisesRegex(qa.EvaluationError, '缺少非空 answer'):
            qa.parse_answer({'done': True, 'message': {'content': json.dumps(echo)}}, [])
        echo.update(answer='假答案', insufficient=False)
        with self.assertRaisesRegex(qa.EvaluationError, '非预期字段'):
            qa.parse_answer({'done': True, 'message': {'content': json.dumps(echo)}}, [])

    def test_prompt_never_includes_labels(self):
        value = qa.messages('test', [dict(id='IT-001', title='t', content='c', score=3)])
        payload = json.loads(value[1]['content'].split('\n', 1)[1])
        self.assertEqual(set(payload), {'question', 'fragments'})
        self.assertEqual(set(payload['fragments'][0]), {'id', 'title', 'content'})
        self.assertIn('资料不足', value[0]['content'])


class StubHandler(BaseHTTPRequestHandler):
    requests = []
    fail_query = None
    fail_mode = 'http'

    def log_message(self, *_):
        pass

    def send(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        try:
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected after the synthetic timeout test client disconnects.

    def do_GET(self):
        self.send({'version': 'TEST-STUB-NOT-OLLAMA'} if self.path == '/api/version'
                  else {'models': [{'name': qa.MODEL, 'digest': 'synthetic-test-digest'}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(payload)
        context = json.loads(payload['messages'][1]['content'].split('\n', 1)[1])
        if context['question'] == self.fail_query:
            if self.fail_mode == 'timeout':
                time.sleep(.15)
                self.send({'done': True})
            else:
                self.send({'error': 'synthetic failure'}, 500)
            return
        self.send({'done': True, 'done_reason': 'stop', 'model': qa.MODEL,
                   'message': {'role': 'assistant', 'content': json.dumps({
                       'answer': '资料不足，无法回答。这是协议测试桩合成文本。',
                       'insufficient': True}, ensure_ascii=False)},
                   'total_duration': 1000, 'eval_count': 1})


class HTTPPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Run the unchanged FastAPI app through an actual TCP HTTP server.
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        cls.proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app',
                                     '--fd', str(sock.fileno())],
                                    cwd=qa.ROOT, pass_fds=(sock.fileno(),),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sock.close()
        cls.retrieval_url = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                qa.request_json(cls.retrieval_url, '/openapi.json', timeout=1)
                break
            except qa.EvaluationError:
                time.sleep(.05)
        else:
            cls.proc.terminate()
            raise RuntimeError('FastAPI did not start')
        cls.stub = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
        cls.thread = threading.Thread(target=cls.stub.serve_forever, daemon=True)
        cls.thread.start()
        cls.ollama_url = f'http://127.0.0.1:{cls.stub.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.stub.shutdown()
        cls.stub.server_close()
        cls.thread.join(timeout=5)
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def args(self, output):
        return SimpleNamespace(output_dir=output, retrieval_url=self.retrieval_url,
                               ollama_url=self.ollama_url, retrieval_timeout=3, ollama_timeout=3)

    def test_all_20_over_real_http_and_manual_review(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            args = self.args(Path(temp) / 'run')
            self.assertEqual(qa.run(args), 0)
            report = json.loads((args.output_dir / 'results.json').read_text())
            self.assertEqual(len(report['rows']), 20)
            self.assertEqual(report['summary']['retrieval_hits'], 14)
            self.assertEqual(report['summary']['unanswerable_with_fragments'], ['Q16', 'Q17', 'Q18'])
            self.assertTrue(all(row['answer_correct'] is None and row['refusal_correct'] is None
                                for row in report['rows']))
            for row in report['rows']:
                self.assertGreaterEqual(row['total_seconds'], row['retrieval_seconds'])
                self.assertGreaterEqual(row['total_seconds'], row['generation_seconds'])
                self.assertIn('ollama_response', row)
                sent = json.loads(row['messages'][1]['content'].split('\n', 1)[1])
                self.assertEqual([f['id'] for f in sent['fragments']], [f['id'] for f in row['fragments']])
            with patch('builtins.input', side_effect=['n', '测试桩没有实际回答', 'q']):
                qa.review(SimpleNamespace(run_dir=args.output_dir))
            graded = json.loads((args.output_dir / 'results.json').read_text())
            self.assertFalse(graded['rows'][0]['answer_correct'])
            self.assertIsNone(graded['rows'][1]['answer_correct'])
            self.assertEqual(graded['summary']['answer_correct']['reviewed'], 1)
            # Finish answerable scoring and separately score the first unanswerable.
            with patch('builtins.input', side_effect=['n', '测试桩不正确'] * 14 + ['y', '明确拒答', 'q']):
                qa.review(SimpleNamespace(run_dir=args.output_dir))
            graded = json.loads((args.output_dir / 'results.json').read_text())
            self.assertEqual(graded['summary']['answer_correct']['reviewed'], 15)
            self.assertEqual(graded['summary']['refusal_correct']['reviewed'], 1)
            self.assertTrue(graded['rows'][15]['refusal_correct'])
            self.assertIsNone(graded['rows'][15]['answer_correct'])
            with self.assertRaises(FileExistsError):
                qa.run(args)
        for request in StubHandler.requests:
            self.assertEqual(request['model'], 'qwen3:4b')
            self.assertIs(request['stream'], False)
            self.assertIs(request['think'], False)
            self.assertEqual(request['options']['temperature'], 0)
            self.assertEqual(request['format'], qa.ANSWER_SCHEMA)

    def test_one_generation_failure_saved_and_remaining_questions_continue(self):
        StubHandler.fail_query = '忘记统一账号密码，如何重置？'
        try:
            with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
                args = self.args(Path(temp) / 'run')
                self.assertEqual(qa.run(args), 1)
                report = json.loads((args.output_dir / 'results.json').read_text())
                self.assertEqual(len(report['rows']), 20)
                self.assertEqual(report['summary']['errors'], ['Q01'])
                self.assertEqual(report['rows'][0]['retrieval_hit'], True)
                self.assertIn('HTTP 500', report['rows'][0]['error'])
                self.assertIsNone(report['rows'][0]['answer_correct'])
        finally:
            StubHandler.fail_query = None

    def test_generation_timeout_preserves_retrieval_and_continues(self):
        StubHandler.fail_query = '忘记统一账号密码，如何重置？'
        StubHandler.fail_mode = 'timeout'
        try:
            with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
                args = self.args(Path(temp) / 'run')
                args.ollama_timeout = .08
                self.assertEqual(qa.run(args), 1)
                report = json.loads((args.output_dir / 'results.json').read_text())
                self.assertEqual(report['summary']['errors'], ['Q01'])
                self.assertTrue(report['rows'][0]['fragments'])
                self.assertIn('timed out', report['rows'][0]['error'])
                self.assertGreater(report['rows'][0]['generation_seconds'], 0)
                self.assertEqual(len(report['rows']), 20)
        finally:
            StubHandler.fail_query = None
            StubHandler.fail_mode = 'http'

    def test_smoke_only_runs_one_question_and_is_not_a_complete_run(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            args = self.args(Path(temp) / 'smoke')
            args.smoke = True
            self.assertEqual(qa.run(args), 0)
            report = json.loads((args.output_dir / 'results.json').read_text())
            self.assertEqual([r['id'] for r in report['rows']], ['Q01'])
            self.assertEqual(report['summary']['run_state'], 'smoke_completed')
            self.assertEqual(report['summary']['answer_correct']['unprocessed'], 14)
            self.assertEqual(report['response_schema']['required'], ['answer', 'insufficient'])

    def test_missing_ollama_records_preflight_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            args = self.args(Path(temp) / 'run')
            with patch('qa_eval.request_json', side_effect=qa.EvaluationError('connection refused')):
                with self.assertRaises(qa.EvaluationError):
                    qa.run(args)
            report = json.loads((args.output_dir / 'results.json').read_text())
            self.assertIn('connection refused', report['preflight_error'])
            self.assertEqual(report['rows'], [])


if __name__ == '__main__':
    unittest.main()
