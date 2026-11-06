"""Temporary local Ollama relay: forward bodies, log only allowlisted metadata."""
import argparse
import http.client
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PATHS = {'/api/chat', '/api/generate', '/api/tags', '/api/show', '/api/version', '/api/ps'}
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length',
               'accept-encoding'}
OPTION_NAMES = {'temperature', 'seed', 'num_predict', 'num_gpu', 'num_thread', 'num_ctx',
                'top_p', 'top_k', 'repeat_penalty', 'repeat_last_n', 'think', 'think_level'}
MAX_BODY = 8 * 1024 * 1024


def scalar(value):
    # Never log arbitrary nested values or potentially credential-bearing objects.
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and len(value) <= 128:
        return value
    return '<omitted>'


def request_metadata(body):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return {'json_object': False}
    if not isinstance(data, dict):
        return {'json_object': False}
    model = data.get('model')
    return dict(json_object=True, model=scalar(model),
                model_has_whitespace=isinstance(model, str) and model != model.strip(),
                think_present='think' in data, think=scalar(data.get('think')),
                stream=scalar(data.get('stream')), format_kind=(
                    'schema' if isinstance(data.get('format'), dict) else scalar(data.get('format'))),
                options={k: scalar(v) for k, v in data.get('options', {}).items() if k in OPTION_NAMES}
                if isinstance(data.get('options'), dict) else {})


class ResponseStats:
    """Inspect bounded JSON/NDJSON fragments in memory; persist no generated text."""
    def __init__(self):
        self.pending = b''
        self.thinking_chars = 0
        self.content_chars = 0
        self.content_has_think_tag = False
        self.content_tail = ''
        self.final = {}
        self.inspectable = True

    def consume(self, data):
        self.pending += data
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            self.parse(line)
        if len(self.pending) > MAX_BODY:
            self.pending = b''
            self.inspectable = False

    def parse(self, data):
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            return
        if not isinstance(value, dict):
            return
        message = value.get('message') if isinstance(value.get('message'), dict) else {}
        thinking = message.get('thinking', value.get('thinking', ''))
        content = message.get('content', value.get('response', ''))
        if isinstance(thinking, str):
            self.thinking_chars += len(thinking)
        if isinstance(content, str):
            self.content_chars += len(content)
            joined = self.content_tail + content
            self.content_has_think_tag |= '<think>' in joined.lower() or '</think>' in joined.lower()
            self.content_tail = joined[-16:]
        if value.get('done') is True:
            for k in ('done', 'done_reason', 'total_duration', 'load_duration', 'prompt_eval_duration',
                      'eval_duration', 'prompt_eval_count', 'eval_count'):
                if k in value:
                    self.final[k] = scalar(value[k])

    def finish(self):
        self.parse(self.pending)
        self.pending = b''
        return dict(thinking_chars=self.thinking_chars, content_chars=self.content_chars,
                    content_has_think_tag=self.content_has_think_tag,
                    response_inspectable=self.inspectable, ollama=self.final)


class ProbeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, upstream_port, log_path, timeout=180):
        super().__init__(address, ProbeHandler)
        self.upstream_port = upstream_port
        self.log_path = Path(log_path)
        self.timeout_seconds = timeout
        self.log_lock = threading.Lock()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **fields):
        line = json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), **fields), ensure_ascii=False)
        with self.log_lock:
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
            print(line, flush=True)


class ProbeHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 close-delimited responses support immediate NDJSON forwarding
    # without buffering the whole generation or inventing chunk boundaries.
    protocol_version = 'HTTP/1.0'

    def log_message(self, *_):
        pass  # Base logging may include arbitrary URL/query data.

    def fail(self, status, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.relay()

    def do_POST(self):
        self.relay()

    def relay(self):
        path = urlsplit(self.path).path
        if path not in PATHS:
            self.fail(404, 'Only Ollama chat/model-inspection endpoints are allowed')
            return
        if self.headers.get('Transfer-Encoding'):
            self.fail(400, 'Send a Content-Length request body')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.fail(400, 'Invalid Content-Length')
            return
        if not 0 <= length <= MAX_BODY:
            self.fail(413, 'Request body too large')
            return
        self.connection.settimeout(self.server.timeout_seconds)
        try:
            body = self.rfile.read(length)
        except (OSError, TimeoutError):
            self.fail(408, 'Request body timeout')
            return
        if len(body) != length:
            self.fail(400, 'Incomplete request body')
            return
        rid = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        self.server.record(event='request', request_id=rid, method=self.command, path=path,
                           **request_metadata(body))
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port,
                                                timeout=self.server.timeout_seconds)
        stats = ResponseStats()
        sent = False
        first_byte = None
        status = None
        error = None
        try:
            excluded = HOP_HEADERS | {h.strip().lower() for h in self.headers.get('Connection', '').split(',')}
            headers = {k: v for k, v in self.headers.items() if k.lower() not in excluded}
            headers['Accept-Encoding'] = 'identity'
            connection.request(self.command, self.path, body=body if length else None, headers=headers)
            response = connection.getresponse()
            status = response.status
            self.send_response(status)
            response_excluded = HOP_HEADERS | {h.strip().lower() for h in response.getheader('Connection', '').split(',')}
            for key, value in response.getheaders():
                if key.lower() not in response_excluded:
                    self.send_header(key, value)
            self.end_headers()
            sent = True
            while True:
                chunk = response.read1(8192)
                if not chunk:
                    break
                if first_byte is None:
                    first_byte = round(time.perf_counter() - started, 6)
                self.wfile.write(chunk)
                self.wfile.flush()
                stats.consume(chunk)
        except (OSError, http.client.HTTPException) as exc:
            error = type(exc).__name__  # No exception text: may contain request data.
            if not sent:
                self.fail(502, 'Cannot reach local Ollama; check Ollama and upstream port')
        finally:
            connection.close()
            self.close_connection = True
            self.server.record(event='response', request_id=rid, path=path, status=status,
                               elapsed_seconds=round(time.perf_counter()-started, 6),
                               first_byte_seconds=first_byte, error=error, **stats.finish())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0', help='Docker Desktop must reach the Mac listener')
    parser.add_argument('--port', type=int, default=11435)
    parser.add_argument('--upstream-port', type=int, default=11434)
    parser.add_argument('--log', type=Path, default=ROOT/'work'/f'ollama-probe-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl')
    args = parser.parse_args()
    if args.port == args.upstream_port or not all(1 <= p <= 65535 for p in (args.port, args.upstream_port)):
        parser.error('Use different valid listener and upstream ports')
    try:
        server = ProbeServer((args.host, args.port), args.upstream_port, args.log)
    except OSError as error:
        parser.exit(1, f'无法启动诊断转发服务：{error}\n')
    print(f'诊断地址：http://host.docker.internal:{args.port}；上游：http://127.0.0.1:{args.upstream_port}', flush=True)
    print(f'日志：{args.log}；请求正文不落盘；Control+C 停止。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
