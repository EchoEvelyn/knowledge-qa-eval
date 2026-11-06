"""Batch evaluation for the three-document Dify knowledge-base workflow."""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import re
import platform
import sys
import time
import unicodedata
import webbrowser
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import dify_eval as de
import manage
import qa_eval as qa

ROOT=Path(__file__).resolve().parent
QUESTIONS=ROOT/'knowledge-base/eval/questions.json'
DOCS=ROOT/'knowledge-base/it-demo'


def normalize(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text).casefold())


def load_cases():
    cases=json.loads(QUESTIONS.read_text(encoding='utf-8'))
    if len(cases)!=20 or len({c['id'] for c in cases})!=20 or sum(c['answerable'] for c in cases)!=15:
        raise qa.EvaluationError('文档知识库题集必须为20题，其中15有答案、5无答案')
    for case in cases:
        if case['answerable'] != bool(case['expected_sources']) or case['answerable'] != bool(case['evidence_groups']):
            raise qa.EvaluationError('题集来源/证据标注不完整：'+case['id'])
        material=''.join((DOCS/name).read_text(encoding='utf-8') for name in case['expected_sources'])
        for group in case['evidence_groups']:
            if not group or not any(normalize(quote) in normalize(material) for quote in group):
                raise qa.EvaluationError('标注证据不在本地资料中：'+case['id'])
    return cases


def unpack(row,response):
    data=response.get('data')
    row['workflow_run_id']=response.get('workflow_run_id') or (data.get('id') if isinstance(data,dict) else None)
    if not isinstance(data,dict):
        raise qa.EvaluationError('Dify 响应缺少data')
    for target,source in [('workflow_id','workflow_id'),('workflow_status','status'),
                          ('workflow_elapsed_seconds','elapsed_time'),('workflow_total_tokens','total_tokens')]:
        row[target]=data.get(source)
    if data.get('status')!='succeeded':
        raise qa.EvaluationError(f"工作流失败：{data.get('status')}；{data.get('error')}")
    outputs=data.get('outputs')
    if not isinstance(outputs,dict):
        raise qa.EvaluationError('工作流缺少outputs')
    body=outputs.get('retrieval_body')
    try:
        retrieval=json.loads(body) if isinstance(body,str) else body
    except ValueError as error:
        raise qa.EvaluationError('retrieval_body不是合法JSON') from error
    if not isinstance(retrieval,dict) or retrieval.get('query')!=row['query'] or retrieval.get('top_k')!=3:
        raise qa.EvaluationError('问题或top_k不符，请发布文档知识库工作流的当前版本')
    row['retrieval_response']=retrieval
    fragments=retrieval.get('results')
    if (not isinstance(fragments,list) or len(fragments)>3 or
        any(not isinstance(f,dict) or not re.fullmatch(r'KB-\d+',str(f.get('id',''))) or
            any(not isinstance(f.get(k),str) or not f[k].strip() for k in ('title','content')) for f in fragments)):
        raise qa.EvaluationError('片段必须包含KB编号、来源文档名title和非空content')
    if len({f['id'] for f in fragments})!=len(fragments):
        raise qa.EvaluationError('本次片段编号重复')
    known={p.name for p in DOCS.glob('*.txt')}
    for fragment in fragments:
        source=fragment['title'].replace('\\','/').rsplit('/',1)[-1]
        if source not in known:
            raise qa.EvaluationError('无法识别来源文档名，请确认组装提示读取metadata.document_name：'+source)
        fragment['source_document']=source
        fragment['evidence_sha256']=hashlib.sha256((source+'\n'+fragment['content']).encode()).hexdigest()
    row['fragments']=fragments
    eligible=[f for f in fragments if f['source_document'] in row['expected_sources']]
    # These aliases are derived from this response, NOT permanent gold chunk IDs.
    row['expected_ids']=[f['id'] for f in eligible]
    row['expected_ids_provenance']='per_request_aliases_from_expected_source_not_stable_gold_ids'
    row['source_hit']=bool(eligible) if row['answerable'] else None
    text=normalize('\n'.join(f['content'] for f in eligible))
    covered=[any(normalize(quote) in text for quote in group) for group in row['evidence_groups']]
    row['evidence_groups_covered']=covered
    row['retrieval_hit']=(bool(eligible) and all(covered)) if row['answerable'] else None
    raw=outputs.get('raw_answer')
    if not isinstance(raw,str):
        raise qa.EvaluationError('缺少raw_answer，请检查Output配置')
    parsed=qa.parse_answer({'done':True,'message':{'content':raw}},fragments)
    if outputs.get('answer')!=parsed['answer'] or outputs.get('insufficient') is not parsed['model_insufficient']:
        raise qa.EvaluationError('输出字段与原始模型JSON不一致')
    cited=sorted(set(re.findall(r'\[\s*([A-Za-z][A-Za-z0-9_-]*)\s*\]',parsed['answer'])))
    returned={f['id'] for f in fragments}
    parsed.update(cited_ids=cited,citation_valid=bool(cited) and set(cited)<=returned,
                  unknown_citations=sorted(set(cited)-returned))
    row.update(parsed)


def render(directory):
    with contextlib.redirect_stdout(io.StringIO()):
        manage.render_report(directory)
    p=directory/'report.html'
    page=p.read_text(encoding='utf-8').replace('<h1>问答评测报告</h1>',
        '<h1>第四阶段：文档知识库批量评测</h1><p>检索命中为正确文档中的标注证据规则覆盖，非原IT-ID命中。'
        'KB编号仅在本次请求内有效。总秒数为客户端端到端耗时。</p>',1)
    p.write_text(page,encoding='utf-8')


def save(directory,report):
    with contextlib.redirect_stdout(io.StringIO()):
        de.save(directory,report)
    report['summary']['source_hits']=sum(r.get('source_hit') is True for r in report['rows'])
    qa.write_json(directory/'results.json',report)
    path=directory/'results.csv'
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream);records=list(reader)
        fields=reader.fieldnames+['source_hit']
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for record,row in zip(records,report['rows']):
            record['source_hit']=row.get('source_hit');writer.writerow(record)
    p=directory/'summary.md'
    text=p.read_text(encoding='utf-8').replace('# 第三阶段 Dify 问答评测','# 第四阶段 文档知识库问答评测',1)
    text+='\n检索口径：正确来源文档的返回正文合并后覆盖全部标注证据组（组内满足任一短语）；仅做NFKC、大小写和空白规范化。这是辅助规则，不是语义评审。\n'
    text+=f"正确来源文档返回数：{report['summary']['source_hits']}/15（未完成题不据此报告完整命中率）。KB-1等只是请求内引用别名，不是稳定金标ID。\n"
    p.write_text(text,encoding='utf-8')
    render(directory)


def run(args,client):
    cases=load_cases()
    directory=args.output_dir or ROOT/'reports/phase4'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True,exist_ok=False)
    files=[QUESTIONS]+sorted(DOCS.glob('*.txt'))
    report=dict(backend='dify-document-kb',stage=4,started_at=qa.utc_now(),smoke=args.smoke,
                model=qa.MODEL,top_k=3,api_base=client.base,python=platform.python_version(),platform=platform.platform(),
                retrieval_config_expected=dict(embedding_model='bge-m3',method='vector',top_k=3),
                configuration_provenance='expected_document_workflow_not_observed_by_api',
                input_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
                               for n in ('kb_eval.py','dify_eval.py','qa_eval.py','manage.py')},rows=[])
    try:
        report['api_parameters']=de.preflight(client)
        try:report['app_info']=client.request('/info')
        except qa.EvaluationError:report['app_info_note']='未获得应用信息；仍需确认使用新知识库应用的密钥'
    except qa.EvaluationError as error:
        report['preflight_error']=str(error)
        save(directory,report)
        raise qa.EvaluationError(f'{error}\n预检结果：{directory}') from error
    failed=False
    try:
        for case in (cases[:1] if args.smoke else cases):
            row=dict(**case,expected_ids=[],fragments=[],retrieval_hit=None,source_hit=None,
                     answer=None,model_insufficient=None,citation_valid=None,answer_correct=None,
                     refusal_correct=None,review_notes='',retrieval_seconds=None,generation_seconds=None,
                     total_seconds=None,status='error',error=None)
            report['rows'].append(row)
            started=time.perf_counter()
            abort=False
            try:
                row['dify_request']=dict(inputs={'query':case['query']},response_mode='blocking',user='knowledge-qa-docs-eval')
                t=time.perf_counter()
                try:response=client.request('/workflows/run',row['dify_request'])
                finally:row['api_seconds']=round(time.perf_counter()-t,6)
                row['dify_response']=response
                unpack(row,response)
                row['status']='ok'
            except qa.EvaluationError as error:
                row['error']=str(error);failed=True
                if isinstance(error,de.DifyHTTPError) and error.status in (401,403):
                    report['aborted_reason']='API权限失效，停止后续请求';abort=True
            finally:
                row['total_seconds']=round(time.perf_counter()-started,6)
                save(directory,report)
            print(f"{case['id']} {row['status']} source_hit={row['source_hit']} evidence_hit={row['retrieval_hit']} {row['total_seconds']:.2f}s",flush=True)
            if row['error']:print('  错误：'+row['error'],flush=True)
            if args.smoke and row['answer']:print('  回答：'+row['answer'],flush=True)
            if abort:break
    except KeyboardInterrupt:
        report['interrupted']=True;failed=True
        if report['rows'] and report['rows'][-1]['status']!='ok':
            report['rows'][-1].update(status='interrupted',error='用户中断')
    report['finished_at']=qa.utc_now()
    save(directory,report)
    print(f'结果目录：{directory}\n评分：python3 kb_eval.py review --run-dir {directory}')
    return 1 if failed else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('check','run'):
        p=sub.add_parser(name)
        p.add_argument('--api-base',default='http://localhost/v1')
        p.add_argument('--timeout',type=qa.positive,default=300)
        if name=='run':
            p.add_argument('--smoke',action='store_true')
            p.add_argument('--output-dir',type=Path)
    for name in ('review','report'):
        p=sub.add_parser(name);p.add_argument('--run-dir',type=Path,required=True)
        if name=='report':p.add_argument('--open',action='store_true')
    args=parser.parse_args()
    try:
        if args.command in ('review','report'):
            directory=manage.find_run(args.run_dir)
            report=json.loads((directory/'results.json').read_text(encoding='utf-8'))
            if report.get('backend')!='dify-document-kb':raise qa.EvaluationError('请选择新文档知识库评测结果')
            if args.command=='review':
                if (directory/'provenance.json').exists():raise qa.EvaluationError('不覆盖归档评分')
                qa.review(SimpleNamespace(run_dir=directory))
                save(directory,json.loads((directory/'results.json').read_text(encoding='utf-8')))
            else:
                render(directory);print('报告：',directory/'report.html')
                if args.open:webbrowser.open((directory/'report.html').resolve().as_uri())
            return 0
        load_cases()
        client=de.Client(args.api_base,de.api_key(),args.timeout)
        if args.command=='check':
            de.preflight(client);print('新题集、API鉴权与query输入预检通过；尚未运行模型。');return 0
        return run(args,client)
    except KeyboardInterrupt:
        print('已中断，已有结果保留。',file=sys.stderr);return 130
    except (qa.EvaluationError,OSError,ValueError,EOFError,KeyError) as error:
        print('错误：'+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
