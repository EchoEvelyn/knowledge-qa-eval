"""One entry point for local preflight, evaluation, review, and offline reports."""
import argparse
import hashlib
import html
import json
import socket
import subprocess
import sys
import time
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import qa_eval as qa

ROOT = Path(__file__).resolve().parent


def ollama_check(url):
    try:
        version = qa.request_json(url, '/api/version', timeout=5)
        tags = qa.request_json(url, '/api/tags', timeout=5)
        if not isinstance(tags, dict) or not isinstance(tags.get('models'), list):
            raise qa.EvaluationError('/api/tags 格式错误')
        model = next((m for m in tags['models'] if isinstance(m, dict) and m.get('name') == qa.MODEL), None)
        if model is None:
            raise qa.EvaluationError(f'请先运行 ollama pull {qa.MODEL}')
        return dict(version=version, model=model)
    except qa.EvaluationError as error:
        raise qa.EvaluationError(f'Ollama 预检失败：{error}。请打开 Mac Ollama 应用后重试。') from error


def retrieval_check(port):
    url = f'http://127.0.0.1:{port}'
    found = qa.request_json(url, '/retrieval', {'query': '忘记密码怎么重置', 'top_k': 3}, timeout=3)
    empty = qa.request_json(url, '/retrieval', {'query': '量子香蕉星际旅行', 'top_k': 3}, timeout=3)
    if (not isinstance(found, dict) or not isinstance(found.get('results'), list) or
            not found['results'] or not isinstance(found['results'][0], dict) or
            found['results'][0].get('id') != 'IT-001' or
            len(found['results']) > 3 or not isinstance(empty, dict) or empty.get('results') != []):
        raise qa.EvaluationError(f'{url} 的检索响应与本项目不符，请检查端口或服务版本')
    return dict(url=url, matched_id='IT-001', no_match_empty=True)


def port_busy(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except ConnectionRefusedError:
        return False


@contextmanager
def retrieval_service(port):
    """Reuse a valid running service; stop only the process started by this command."""
    if port_busy(port):
        info = retrieval_check(port)
        print(f'复用检索服务：{info["url"]}', flush=True)
        yield dict(**info, managed=False)
        return
    log_dir = ROOT / 'work'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f'retrieval-{datetime.now():%Y%m%d-%H%M%S-%f}.log'
    with log_path.open('w', encoding='utf-8') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app',
                                 '--host', '127.0.0.1', '--port', str(port)],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            last_error = ''
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise qa.EvaluationError(f'检索服务启动失败，查看日志：{log_path}')
                try:
                    info = retrieval_check(port)
                    break
                except qa.EvaluationError as error:
                    last_error = str(error)
                    time.sleep(.1)
            else:
                raise qa.EvaluationError(f'检索服务未就绪：{last_error}；日志：{log_path}')
            print(f'自动启动检索服务：{info["url"]}；日志：{log_path}', flush=True)
            yield dict(**info, managed=True, log=str(log_path))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def find_run(value):
    if value:
        directory = value.resolve()
        if not (directory / 'results.json').is_file():
            raise qa.EvaluationError(f'未找到 {directory}/results.json')
        return directory
    # Only newly managed runs; archived Mac baseline is selected explicitly.
    candidates = list((ROOT / 'reports/phase2').glob('*/results.json'))
    if not candidates:
        raise qa.EvaluationError('没有本地运行结果，请先 run 或使用 --run-dir 指定已有结果')
    return max(candidates, key=lambda p: p.stat().st_mtime_ns).parent


def render_report(directory):
    """Read source JSON without modifying it; escape all imported answer text."""
    source = directory / 'results.json'
    report = json.loads(source.read_text(encoding='utf-8'))
    rows = report['rows']
    answerable = [r for r in rows if r['answerable']]
    unanswerable = [r for r in rows if not r['answerable']]
    hits = sum(r['retrieval_hit'] is True for r in answerable)
    errors = [r['id'] for r in rows if r['status'] != 'ok']
    metrics = [('检索命中', f'{hits}/15')]
    for label, key, items, total in [('回答正确', 'answer_correct', answerable, 15),
                                    ('正确拒答', 'refusal_correct', unanswerable, 5)]:
        correct = sum(r[key] is True for r in items)
        reviewed = sum(r[key] is not None for r in items)
        metrics.append((label, f'{correct}/{total}（已评 {reviewed}/{total}）'))
    timings = {}
    for key in ('retrieval_seconds', 'generation_seconds', 'total_seconds'):
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        timings[key] = dict(count=len(values), mean=round(sum(values)/len(values), 6) if values else None,
                            total=round(sum(values), 6))
    e = lambda value: html.escape(str(value))
    tr = []
    def verdict(value):
        return '待评/不适用' if value is None else '正确' if value else '错误'
    for r in rows:
        fragments = '\n\n'.join(f"[{f['id']}] {f['title']}\n{f['content']}" for f in r['fragments'])
        tr.append(f"<tr><td>{e(r['id'])}</td><td>{e(r['query'])}</td>"
                  f"<td>{e(r['retrieval_hit'])}</td><td>{e(r.get('answer') or '')}"
                  f"<details><summary>检索片段</summary><pre>{e(fragments)}</pre></details></td>"
                  f"<td>{e(verdict(r['answer_correct']))}</td><td>{e(verdict(r['refusal_correct']))}</td>"
                  f"<td>{e(r.get('total_seconds'))}</td><td>{e(r.get('review_notes', ''))}"
                  f"<br>{e(r.get('error') or '')}</td></tr>")
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>问答评测报告</title>'
    page += '''<style>body{font:16px system-ui;margin:32px;color:#17212b}table{border-collapse:collapse;width:100%}
    td,th{border:1px solid #ccd4dc;padding:10px;text-align:left;vertical-align:top}th{background:#eef3f7}
    pre{white-space:pre-wrap}details{margin-top:12px}small{color:#52606d}</style><h1>问答评测报告</h1>'''
    page += f"<p>模型：{e(report['model'])}；top_k={e(report['top_k'])}；处理 {len(rows)}/20；错误题：{e(errors)}</p>"
    page += f"<p>状态：{e(report.get('summary', {}).get('run_state', '未知'))}；预检错误：{e(report.get('preflight_error', '无'))}</p>"
    page += ''.join(f'<p><b>{e(k)}</b>：{e(v)}</p>' for k, v in metrics)
    page += f'<p>耗时统计（秒）：{e(json.dumps(timings, ensure_ascii=False))}</p>'
    page += '<p>单题试跑或未完成评分不能当作完整正确率；语义评分来自人工判定。</p>'
    page += '<table><thead><tr>' + ''.join(f'<th>{v}</th>' for v in ['题号','问题','检索命中','回答及资料','回答正确','拒答正确','总秒数','评分理由/错误']) + '</tr></thead><tbody>'
    page += ''.join(tr) + '</tbody></table>'
    page += f'<p><small>来源 JSON SHA256：{hashlib.sha256(source.read_bytes()).hexdigest()}</small></p></html>'
    path = directory / 'report.html'
    path.write_text(page, encoding='utf-8')
    print(f'结果目录：{directory}')
    print('；'.join(f'{label}：{value}' for label, value in metrics))
    print(f'报告：{path}')
    return path


def execute(args):
    if args.command == 'report':
        path = render_report(find_run(args.run_dir))
        if args.open:
            webbrowser.open(path.as_uri())
        return 0
    if args.command == 'review':
        directory = find_run(args.run_dir)
        if (directory / 'provenance.json').exists():
            raise qa.EvaluationError('这是保留原始证据的归档目录，不修改评分；重新评测请使用新目录')
        result = qa.review(SimpleNamespace(run_dir=directory))
        render_report(directory)
        return result
    # Verify Ollama before starting a retrieval child; never install/download models silently.
    ollama = ollama_check(args.ollama_url)
    if args.command == 'check':
        retrieval = retrieval_check(args.port)
        print(f'预检通过：Ollama {ollama["version"]}，模型 {qa.MODEL}，检索 {retrieval["url"]}')
        return 0
    directory = args.output_dir or ROOT / 'reports/phase2' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if directory.exists():
        raise qa.EvaluationError(f'结果目录已存在，请使用新目录：{directory}')
    with retrieval_service(args.port) as service:
        options = SimpleNamespace(output_dir=directory, retrieval_url=service['url'],
                                  ollama_url=args.ollama_url, retrieval_timeout=10,
                                  ollama_timeout=args.ollama_timeout, smoke=args.smoke)
        # Record both runtime and code identity, including ZIP installations without .git.
        try:
            code = qa.run(options)
        finally:
            if (directory / 'results.json').exists():
                qa.write_json(directory / 'workflow.json', dict(
                    entrypoint='manage.py', checked_at=qa.utc_now(), retrieval=service,
                    ollama=ollama, source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                      for name in ('manage.py', 'qa_eval.py', 'app.py', 'requirements.txt')}))
    if args.review and code == 0:
        qa.review(SimpleNamespace(run_dir=directory))
    render_report(directory)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'run'):
        p = sub.add_parser(name)
        p.add_argument('--ollama-url', default='http://127.0.0.1:11434')
        p.add_argument('--port', type=int, choices=range(1, 65536), metavar='PORT', default=8765)
        if name == 'run':
            p.add_argument('--output-dir', type=Path)
            p.add_argument('--ollama-timeout', type=qa.positive, default=180)
            p.add_argument('--smoke', action='store_true')
            p.add_argument('--review', action='store_true', help='生成完成后在当前终端逐题人工评分')
    for name in ('review', 'report'):
        p = sub.add_parser(name)
        p.add_argument('--run-dir', type=Path, help='省略时选择 reports/phase2 中最近更新的结果')
        if name == 'report':
            p.add_argument('--open', action='store_true', help='用默认浏览器打开离线 HTML 报告')
    try:
        return execute(parser.parse_args())
    except KeyboardInterrupt:
        print('已中断；已保存的结果保留。', file=sys.stderr)
        return 130
    except (qa.EvaluationError, OSError, ValueError, KeyError, EOFError) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
