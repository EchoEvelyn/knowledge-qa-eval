"""Workflow lifecycle tests; the model service is an explicitly synthetic stub."""
import contextlib
import hashlib
import io
import json
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import manage
import qa_eval as qa
from test_qa_eval import StubHandler


def available_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class WorkflowStubHandler(StubHandler):
    def do_POST(self):
        if self.path != '/api/chat':
            self.send({'error': 'synthetic non-retrieval service'}, 404)
            return
        super().do_POST()


class WorkflowHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), WorkflowStubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def options(self, directory, port):
        return SimpleNamespace(command='run', port=port, ollama_url=self.url,
                               output_dir=directory, ollama_timeout=3, smoke=True, review=False)

    def test_run_starts_and_stops_owned_service_and_generates_report(self):
        port = available_port()
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temp)/'run'
            self.assertEqual(manage.execute(self.options(directory, port)), 0)
            self.assertFalse(manage.port_busy(port))
            workflow = json.loads((directory/'workflow.json').read_text())
            self.assertTrue(workflow['retrieval']['managed'])
            self.assertEqual(workflow['ollama']['version']['version'], 'TEST-STUB-NOT-OLLAMA')
            self.assertIn('qa_eval.py', workflow['source_sha256'])
            self.assertTrue((directory/'report.html').is_file())
            self.assertIn('smoke_completed', (directory/'report.html').read_text())

    def test_check_and_run_reuse_service_without_stopping_it(self):
        port = available_port()
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            with manage.retrieval_service(port):
                self.assertEqual(manage.execute(SimpleNamespace(command='check', port=port, ollama_url=self.url)), 0)
                directory = Path(temp)/'run'
                self.assertEqual(manage.execute(self.options(directory, port)), 0)
                self.assertTrue(manage.port_busy(port))
                self.assertFalse(json.loads((directory/'workflow.json').read_text())['retrieval']['managed'])
            self.assertFalse(manage.port_busy(port))

    def test_wrong_service_on_port_is_rejected_without_terminating_it(self):
        with self.assertRaises(qa.EvaluationError):
            with manage.retrieval_service(self.server.server_port):
                self.fail('Must not reuse a non-retrieval service')
        self.assertTrue(manage.port_busy(self.server.server_port))

    def test_missing_ollama_does_not_start_retrieval(self):
        with tempfile.TemporaryDirectory() as temp:
            args = self.options(Path(temp)/'run', available_port())
            with patch('manage.ollama_check', side_effect=qa.EvaluationError('missing')), patch('manage.retrieval_service') as service:
                with self.assertRaises(qa.EvaluationError):
                    manage.execute(args)
                service.assert_not_called()
                self.assertFalse(args.output_dir.exists())

    def test_model_failure_keeps_results_and_stops_owned_service(self):
        StubHandler.fail_query = '忘记统一账号密码，如何重置？'
        port = available_port()
        try:
            with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
                directory = Path(temp)/'run'
                self.assertEqual(manage.execute(self.options(directory, port)), 1)
                self.assertFalse(manage.port_busy(port))
                self.assertTrue((directory/'workflow.json').exists())
                self.assertIn('HTTP 500', (directory/'report.html').read_text())
        finally:
            StubHandler.fail_query = None


class OfflineReportTests(unittest.TestCase):
    def test_mac_archive_integrity_and_complete_review(self):
        directory = qa.ROOT/'reports/mac-run-02'
        manifest = json.loads((directory/'provenance.json').read_text())
        for name, record in manifest['files'].items():
            self.assertEqual(hashlib.sha256((directory/name).read_bytes()).hexdigest(), record['sha256'])
        report = json.loads((directory/'results.json').read_text())
        self.assertEqual(len(report['rows']), 20)
        self.assertEqual(sum(r['answer_correct'] is True for r in report['rows']), 13)
        self.assertEqual(sum(r['refusal_correct'] is True for r in report['rows']), 1)
        with self.assertRaises(qa.EvaluationError):
            manage.execute(SimpleNamespace(command='review', run_dir=directory))

    def test_html_escapes_text_and_does_not_modify_source(self):
        report = json.loads((qa.ROOT/'reports/mac-run-02/results.json').read_text())
        report['rows'][0]['answer'] = '<script>alert("unsafe")</script>'
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temp)
            source = directory/'results.json'
            qa.write_json(source, report)
            before = source.read_bytes()
            path = manage.render_report(directory)
            self.assertEqual(source.read_bytes(), before)
            page = path.read_text()
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertIn('13/15', page)
            self.assertIn('1/5', page)


if __name__ == '__main__':
    unittest.main()
