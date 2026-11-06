"""Evaluate the published aligned Dify workflow using the original 20 cases."""
import argparse
import contextlib
import io
import csv
import getpass
import hashlib
import json
import os
import platform
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import qa_eval as qa
import manage

ROOT = Path(__file__).resolve().parent


class DifyHTTPError(qa.EvaluationError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Do not forward an API credential to a redirected destination.


class Client:
    def __init__(self, base, key, timeout):
        parsed = urlsplit(base)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/v1'):
            raise qa.EvaluationError('API 地址应类似 http://localhost/v1，不包含密钥或查询参数')
        self.base, self.key, self.timeout = base.rstrip('/'), key, timeout
        handlers = [NoRedirect()]
        if parsed.hostname in ('localhost','127.0.0.1','::1'):
            handlers.append(ProxyHandler({}))
        self.http = build_opener(*handlers)

    def clean(self, value):
        if isinstance(value, str):
            return value.replace(self.key, '[REDACTED]')
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {k: ('[REDACTED]' if k.lower() in ('authorization','api_key','access_token','secret')
                        else self.clean(v)) for k,v in value.items()}
        return value

    def request(self, path, payload=None):
        request = Request(self.base + path,
                          data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode(),
                          headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        try:
            with self.http.open(request, timeout=self.timeout) as response:
                value = json.load(response)
            if not isinstance(value, dict):
                raise qa.EvaluationError('Dify 响应应为 JSON 对象')
            return self.clean(value)
        except HTTPError as error:
            # Record bounded diagnostics, never the request Authorization header.
            try:
                detail = self.clean(error.read(2000).decode('utf-8',errors='replace'))
            finally:
                error.close()
            raise DifyHTTPError(error.code, f'{path}: HTTP {error.code}: {detail}') from error
        except (URLError, OSError, ValueError) as error:
            raise qa.EvaluationError(f'{path}: {self.clean(str(error))}') from error


def api_key():
    key = os.environ.get('DIFY_API_KEY','').strip()
    if not key:
        key = getpass.getpass('请输入 Dify API Key（输入不显示，不保存）：').strip()
    if not key or '\n' in key or '\r' in key:
        raise qa.EvaluationError('需要有效的 Dify API Key')
    return key


def preflight(client):
    parameters = client.request('/parameters')
    fields = parameters.get('user_input_form',[])
    if not isinstance(fields,list):
        raise qa.EvaluationError('Dify user_input_form 格式错误')
    has_query = any(isinstance(field,dict) and
                    (field.get('variable') == 'query' or any(isinstance(v,dict) and v.get('variable') == 'query'
                                                           for v in field.values())) for field in fields)
    if not has_query:
        raise qa.EvaluationError('API 未发现 query 输入；请确认密钥属于已发布的 aligned 工作流')
    return parameters


def unpack(row, response, corpus):
    data = response.get('data')
    row['workflow_run_id'] = response.get('workflow_run_id') or (data.get('id') if isinstance(data,dict) else None)
    if not isinstance(data,dict):
        raise qa.EvaluationError('Dify 响应缺少 data 对象')
    row['workflow_id'] = data.get('workflow_id')
    row['workflow_status'] = data.get('status')
    row['workflow_elapsed_seconds'] = data.get('elapsed_time')
    row['workflow_total_tokens'] = data.get('total_tokens')
    if data.get('status') != 'succeeded':
        raise qa.EvaluationError(f"Dify 工作流未成功：{data.get('status')}；{data.get('error')}")
    outputs = data.get('outputs')
    if not isinstance(outputs,dict):
        raise qa.EvaluationError('Dify outputs 必须是对象')
    body = outputs.get('retrieval_body')
    try:
        retrieval = json.loads(body) if isinstance(body,str) else body
    except ValueError as error:
        raise qa.EvaluationError('retrieval_body 不是合法 JSON') from error
    if (not isinstance(retrieval,dict) or retrieval.get('query') != row['query'] or
            retrieval.get('top_k') != 3):
        raise qa.EvaluationError('检索问题或 top_k 与本题不一致；请发布最新版 aligned 工作流')
    row['retrieval_response'] = retrieval
    fragments = retrieval.get('results')
    if (not isinstance(fragments,list) or len(fragments)>3 or
            any(not isinstance(f,dict) or not isinstance(f.get('id'),str) for f in fragments)):
        raise qa.EvaluationError('检索片段格式错误')
    if len({f['id'] for f in fragments}) != len(fragments):
        raise qa.EvaluationError('检索片段 ID 重复')
    for fragment in fragments:
        expected = corpus.get(fragment['id'])
        if expected is None or any(fragment.get(k) != expected[k] for k in ('title','content')):
            raise qa.EvaluationError('返回的检索资料与固定本地语料不一致')
    row['fragments'] = fragments
    row['retrieval_hit'] = bool(set(row['expected_ids']) & {f['id'] for f in fragments}) if row['answerable'] else None
    # Reuse the same strict answer parser and citation rules as the local baseline.
    raw = outputs.get('raw_answer')
    if not isinstance(raw,str):
        raise qa.EvaluationError('缺少 raw_answer，请导入并发布 aligned 工作流')
    parsed = qa.parse_answer({'done':True,'message':{'content':raw}},fragments)
    if outputs.get('answer') != parsed['answer'] or outputs.get('insufficient') is not parsed['model_insufficient']:
        raise qa.EvaluationError('解析后的输出与 raw_answer 不一致')
    row.update(parsed)


def save(directory, report):
    qa.export(directory,report)
    path=directory/'summary.md'
    summary=path.read_text(encoding='utf-8').replace('# 第二阶段问答评测','# 第三阶段 Dify 问答评测',1)
    summary += ('\n调用方式：已发布的 Dify Workflow API（blocking），非脚本直接调用检索或 Ollama。\n'
                'total_seconds 是客户端请求及本地校验耗时；workflow_elapsed_seconds 是 Dify 工作流耗时。'
                'API 未提供检索/模型分段耗时，retrieval_seconds 和 generation_seconds 留空。\n'
                'model/top_k 为本轮预期配置，API 不直接证明内部模型参数；应以已发布工作流和诊断记录核对。\n')
    if report.get('aborted_reason'):
        summary += '\n批量中止：'+report['aborted_reason']+'\n'
    path.write_text(summary,encoding='utf-8')
    path=directory/'results.csv'
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        records=list(reader)
        fields=reader.fieldnames+['workflow_run_id','workflow_elapsed_seconds','workflow_total_tokens']
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
        writer.writeheader()
        for record,row in zip(records,report['rows']):
            record.update({key:row.get(key) for key in fields[-3:]})
            writer.writerow(record)
    with contextlib.redirect_stdout(io.StringIO()):
        manage.render_report(directory)
    path=directory/'report.html'
    page=path.read_text(encoding='utf-8').replace('<h1>问答评测报告</h1>',
        '<h1>第三阶段 Dify 问答评测</h1><p>总秒数为客户端端到端耗时，非 Ollama 生成耗时。'
        '工作流运行 ID 和服务端耗时见 results.json / results.csv。</p>',1)
    path.write_text(page,encoding='utf-8')


def run(args,client):
    cases=json.loads((ROOT/'data/questions.json').read_text(encoding='utf-8'))
    rubrics=json.loads((ROOT/'data/answer_rubrics.json').read_text(encoding='utf-8'))
    corpus={f['id']:f for f in json.loads((ROOT/'data/corpus.json').read_text(encoding='utf-8'))}
    if len(cases)!=20 or sum(c['answerable'] for c in cases)!=15 or set(rubrics)!={c['id'] for c in cases}:
        raise qa.EvaluationError('原固定题集或评分标准不完整')
    directory=args.output_dir or ROOT/'reports/phase3'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True,exist_ok=False)
    report=dict(backend='dify',stage=3,started_at=qa.utc_now(),smoke=args.smoke,
                model=qa.MODEL,top_k=3,configuration_provenance='expected_aligned_workflow_not_observed_by_api',
                api_base=client.base,python=platform.python_version(),platform=platform.platform(),
                input_sha256={n:hashlib.sha256((ROOT/'data'/n).read_bytes()).hexdigest()
                              for n in ('questions.json','corpus.json','answer_rubrics.json')},
                source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
                               for n in ('dify_eval.py','qa_eval.py','manage.py')},rows=[])
    try:
        report['api_parameters']=preflight(client)
        try:
            report['app_info']=client.request('/info')
        except qa.EvaluationError:
            report['app_info_note']='此版本未返回应用信息；不影响 query 输入预检'
    except qa.EvaluationError as error:
        report['preflight_error']=str(error)
        save(directory,report)
        raise qa.EvaluationError(f'{error}\n预检记录保存在 {directory}') from error
    failed=False
    try:
        for case in (cases[:1] if args.smoke else cases):
            row=dict(**case,rubric=rubrics[case['id']],fragments=[],retrieval_hit=None,answer=None,
                     model_insufficient=None,citation_valid=None,answer_correct=None,refusal_correct=None,
                     review_notes='',retrieval_seconds=None,generation_seconds=None,total_seconds=None,
                     status='error',error=None)
            report['rows'].append(row)
            started=time.perf_counter()
            abort=False
            try:
                payload=dict(inputs={'query':case['query']},response_mode='blocking',user='knowledge-qa-eval')
                row['dify_request']=payload  # No Authorization header or credential stored.
                t=time.perf_counter()
                try:
                    response=client.request('/workflows/run',payload)
                finally:
                    row['api_seconds']=round(time.perf_counter()-t,6)
                row['dify_response']=response
                unpack(row,response,corpus)
                row['status']='ok'
            except qa.EvaluationError as error:
                row['error']=str(error)
                failed=True
                if isinstance(error,DifyHTTPError) and error.status in (401,403):
                    report['aborted_reason']='API 权限失效，保留已完成题目，不继续重复请求'
                    abort=True
            finally:
                row['total_seconds']=round(time.perf_counter()-started,6)
                save(directory,report)
            print(f"{case['id']} {row['status']} retrieval_hit={row['retrieval_hit']} {row['total_seconds']:.2f}s",flush=True)
            if row['error']:
                print('  错误：'+row['error'],flush=True)
            if args.smoke and row['answer']:
                print('  回答：'+row['answer'],flush=True)
            if abort:
                break
    except KeyboardInterrupt:
        report['interrupted']=True
        failed=True
        if report['rows'] and report['rows'][-1]['status']!='ok':
            report['rows'][-1].update(status='interrupted',error='用户中断')
    report['finished_at']=qa.utc_now()
    save(directory,report)
    print(f'结果目录：{directory}\n评分：python dify_eval.py review --run-dir {directory}')
    return 1 if failed else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('check','run'):
        command=sub.add_parser(name)
        command.add_argument('--api-base',default='http://localhost/v1')
        command.add_argument('--timeout',type=qa.positive,default=180)
        if name=='run':
            command.add_argument('--smoke',action='store_true')
            command.add_argument('--output-dir',type=Path)
    for name in ('review','report'):
        command=sub.add_parser(name)
        command.add_argument('--run-dir',type=Path,required=True)
        if name=='report':
            command.add_argument('--open',action='store_true')
    args=parser.parse_args()
    try:
        if args.command in ('review','report'):
            directory=manage.find_run(args.run_dir)
            report=json.loads((directory/'results.json').read_text(encoding='utf-8'))
            if report.get('backend')!='dify' or (directory/'provenance.json').exists():
                raise qa.EvaluationError('请选择本次 Dify 运行目录，不修改本地基线或归档')
            if args.command=='review':
                qa.review(SimpleNamespace(run_dir=directory))
                report=json.loads((directory/'results.json').read_text(encoding='utf-8'))
            save(directory,report)
            if args.command=='report' and args.open:
                webbrowser.open((directory/'report.html').resolve().as_uri())
            return 0
        client=Client(args.api_base,api_key(),args.timeout)
        if args.command=='check':
            preflight(client)
            print('API 鉴权及 query 输入预检通过；尚未执行工作流。')
            return 0
        return run(args,client)
    except KeyboardInterrupt:
        print('已中断；已有结果保留。',file=sys.stderr)
        return 130
    except (qa.EvaluationError,OSError,ValueError,EOFError) as error:
        print(f'错误：{error}',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
