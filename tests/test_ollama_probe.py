"""Real loopback HTTP tests with a synthetic upstream, never real model timings."""
import contextlib
import io
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from ollama_probe import ProbeServer, ResponseStats, request_metadata

CLIENT = build_opener(ProxyHandler({}))
FIRST = b'{"message":{"thinking":"secret reasoning"},"done":false}\n'
LAST = b'{"message":{"content":"secret answer"},"done":true,"load_duration":100,"eval_count":8}\n'


class Upstream(BaseHTTPRequestHandler):
    received = []
    release = threading.Event()

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"version":"synthetic"}')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.received.append((body, self.headers.get('Authorization')))
        self.send_response(400 if self.path == '/api/generate' else 200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        if self.path == '/api/show':
            self.wfile.write(b'{"ok":true}')
        else:
            self.wfile.write(FIRST)
            self.wfile.flush()
            self.release.wait(timeout=3)
            self.wfile.write(LAST)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name)/'log.jsonl'
        Upstream.received = []
        Upstream.release.clear()
        self.capture = contextlib.redirect_stdout(io.StringIO())
        self.capture.__enter__()
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.proxy = ProbeServer(('127.0.0.1', 0), self.upstream.server_port, self.log, timeout=3)
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.upstream, self.proxy)]
        for thread in self.threads:
            thread.start()
        self.url = f'http://127.0.0.1:{self.proxy.server_port}'

    def tearDown(self):
        Upstream.release.set()
        for server in (self.proxy, self.upstream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.capture.__exit__(None, None, None)
        self.temp.cleanup()

    def request(self, path, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        req = Request(self.url+path, data=body,
                      headers={'Content-Type':'application/json', 'Authorization':'Bearer secret-key'})
        return CLIENT.open(req, timeout=5), body

    def test_streaming_bytes_forwarded_before_generation_finishes(self):
        response, body = self.request('/api/chat', {'model':'qwen3:4b','think':False,'stream':True,
                    'messages':[{'role':'user','content':'secret prompt'}],
                    'options':{'temperature':0,'seed':42,'num_predict':2048,'secret':'hidden'}})
        with response:
            self.assertEqual(response.readline(), FIRST)
            self.assertFalse(Upstream.release.is_set())
            Upstream.release.set()
            self.assertEqual(response.read(), LAST)
        self.proxy.shutdown()  # Wait until handler metadata is available below.
        for thread in threading.enumerate():
            if 'process_request_thread' in thread.name:
                thread.join(timeout=1)
        self.assertEqual(Upstream.received[0], (body,'Bearer secret-key'))
        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        request, result = records
        self.assertIs(request['think'], False)
        self.assertIs(request['think_present'], True)
        self.assertEqual(result['thinking_chars'], len('secret reasoning'))
        self.assertEqual(result['ollama']['load_duration'],100)
        text = self.log.read_text()
        for secret in ('secret prompt','secret answer','secret reasoning','secret-key','hidden'):
            self.assertNotIn(secret,text)

    def test_model_validation_endpoint_and_missing_think(self):
        response, body = self.request('/api/show', {'model':'\tqwen3:4b'})
        with response:
            self.assertEqual(response.read(), b'{"ok":true}')
        self.assertEqual(Upstream.received[0][0],body)
        metadata = request_metadata(body)
        self.assertTrue(metadata['model_has_whitespace'])
        self.assertFalse(metadata['think_present'])
        with CLIENT.open(self.url+'/api/version', timeout=3) as response:
            self.assertEqual(json.load(response), {'version':'synthetic'})

    def test_upstream_error_status_and_body_preserved(self):
        Upstream.release.set()
        with self.assertRaises(HTTPError) as raised:
            self.request('/api/generate', {'model':'qwen3:4b'})
        with raised.exception as response:
            self.assertEqual(response.code,400)
            self.assertEqual(response.read(),FIRST+LAST)

    def test_unavailable_upstream_returns_502_and_disallows_other_paths(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            self.proxy.upstream_port=sock.getsockname()[1]
            with self.assertRaises(HTTPError) as raised:
                self.request('/api/chat', {'model':'qwen3:4b'})
            self.assertEqual(raised.exception.code,502)
            raised.exception.close()
        with self.assertRaises(HTTPError) as raised:
            CLIENT.open(self.url+'/api/delete',timeout=3)
        self.assertEqual(raised.exception.code,404)
        raised.exception.close()

    def test_inline_think_tags_and_final_json_metadata(self):
        stats=ResponseStats()
        data=json.dumps({'message':{'content':'<think>private text</think>answer'},
                         'done':True,'eval_duration':123}).encode()
        stats.consume(data[:12]);stats.consume(data[12:])
        result=stats.finish()
        self.assertTrue(result['content_has_think_tag'])
        self.assertEqual(result['ollama']['eval_duration'],123)
        self.assertNotIn('private text',json.dumps(result))


if __name__=='__main__':
    unittest.main()
