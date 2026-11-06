"""Auditable retrieval/answer metrics; strict Recall requires complete corpus qrels."""
import argparse
import csv
import getpass
import hashlib
import html
import json
import math
import os
import statistics
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import dify_eval as de
import kb_eval as kb
import qa_eval as qa

ROOT=Path(__file__).resolve().parent


def fingerprint(source,content):
    return hashlib.sha256((source+'\n'+content).encode()).hexdigest()


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))


def write_csv(path,rows,fields):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(rows)


def page_all(client,path):
    items=[];page=1
    while True:
        value=client.request(path+('?' if '?' not in path else '&')+urlencode({'page':page,'limit':100}))
        data=value.get('data')
        if not isinstance(data,list):raise qa.EvaluationError('分页响应缺少data列表：'+path)
        items.extend(data)
        if not value.get('has_more',False):break
        if not data:raise qa.EvaluationError('分页标记有下一页但内容为空')
        page+=1
        if page>10000:raise qa.EvaluationError('分页超过限制')
    return items


def export_corpus(client,dataset_ids,out):
    out.mkdir(parents=True,exist_ok=False)
    segments=[];documents=[]
    # Only completed enabled documents/segments form the searchable universe.
    for dataset_id in dataset_ids:
        if not all(c.isalnum() or c in '-_' for c in dataset_id):raise qa.EvaluationError('非法知识库ID')
        for doc in page_all(client,f'/datasets/{dataset_id}/documents'):
            if not doc.get('enabled',True) or doc.get('archived',False):continue
            if doc.get('indexing_status')!='completed':raise qa.EvaluationError('存在未索引完成文档：'+str(doc.get('name')))
            did=doc.get('id')
            if not isinstance(did,str) or not all(c.isalnum() or c in '-_' for c in did):raise qa.EvaluationError('非法文档ID')
            documents.append(dict(dataset_id=dataset_id,document_id=did,name=doc.get('name')))
            for segment in page_all(client,f'/datasets/{dataset_id}/documents/{did}/segments'):
                if not segment.get('enabled',True):continue
                if segment.get('status') not in (None,'completed'):raise qa.EvaluationError('存在未完成片段')
                sid=segment.get('id');content=segment.get('content')
                if not isinstance(sid,str) or not isinstance(content,str):raise qa.EvaluationError('片段缺少ID或正文')
                segments.append(dict(stable_id=f'dify:{dataset_id}:{did}:{sid}',dataset_id=dataset_id,
                    document_id=did,segment_id=sid,source_document=doc.get('name'),content=content,
                    evidence_sha256=fingerprint(doc.get('name',''),content),position=segment.get('position')))
    names={d['name'] for d in documents}
    expected={name for c in kb.load_cases() for name in c['expected_sources']}
    if not expected<=names:raise qa.EvaluationError('所选知识库缺少资料：'+str(sorted(expected-names)))
    if not segments or len({s['stable_id'] for s in segments})!=len(segments):raise qa.EvaluationError('片段为空或ID重复')
    corpus=dict(exported_at=qa.utc_now(),complete=True,scope='selected_datasets_enabled_completed_segments',
                dataset_ids=dataset_ids,documents=documents,segments=segments,
                note='导出期间应停止修改、重新分段和索引；这是导出时快照，非数据库事务快照。')
    qa.write_json(out/'segments.json',corpus)
    print(f'导出 {len(documents)}份文档、{len(segments)}个片段：{out}/segments.json')
    return corpus


def prepare(corpus_path,out):
    corpus=json.loads(corpus_path.read_text(encoding='utf-8'))
    if corpus.get('complete') is not True:raise qa.EvaluationError('需要全量片段快照')
    out.mkdir(parents=True,exist_ok=False)
    cases=[c for c in kb.load_cases() if c['answerable']]
    pairs=[]
    for c in cases:
        for segment in corpus['segments']:
            same=segment['source_document'] in c['expected_sources']
            match=sum(any(kb.normalize(q) in kb.normalize(segment['content']) for q in group) for group in c['evidence_groups']) if same else 0
            pairs.append(dict(question_id=c['id'],query=c['query'],stable_id=segment['stable_id'],
                source_document=segment['source_document'],content=segment['content'],
                suggested_only=2 if match else 0,relevance='',reviewer='',reason=''))
    write_csv(out/'qrels.csv',pairs,list(pairs[0]))
    qa.write_json(out/'review-manifest.json',dict(corpus_sha256=hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        questions_sha256=hashlib.sha256(kb.QUESTIONS.read_bytes()).hexdigest(),
        pair_count=len(pairs),instructions='逐对审查全部有答案题×全部片段。relevance=0无关/1仅背景/2支持至少一个核心答案事实，>=2为相关。suggested_only不是已完成标注。'))
    print('待审核金标：',out/'qrels.csv')


def strict_qrels(corpus_path,qrels_dir,rows):
    corpus=json.loads(corpus_path.read_text(encoding='utf-8'))
    manifest=json.loads((qrels_dir/'review-manifest.json').read_text(encoding='utf-8'))
    if corpus.get('complete') is not True:raise qa.EvaluationError('快照不完整，不能算Recall')
    if manifest['corpus_sha256']!=hashlib.sha256(corpus_path.read_bytes()).hexdigest():raise qa.EvaluationError('片段快照与金标不一致')
    if manifest['questions_sha256']!=hashlib.sha256(kb.QUESTIONS.read_bytes()).hexdigest():raise qa.EvaluationError('题集与金标不一致')
    segments={s['stable_id']:s for s in corpus['segments']}
    if len(segments)!=len(corpus['segments']):raise qa.EvaluationError('快照片段ID重复')
    case_ids={r['id'] for r in rows if r['answerable']}
    expected={(qid,sid) for qid in case_ids for sid in segments}
    if manifest.get('qrels_sha256') and manifest['qrels_sha256']!=hashlib.sha256((qrels_dir/'qrels.csv').read_bytes()).hexdigest():raise qa.EvaluationError('金标表哈希与审核manifest不符')
    pairs=read_csv(qrels_dir/'qrels.csv');labels={}
    for pair in pairs:
        key=(pair['question_id'],pair['stable_id'])
        if key in labels or pair['relevance'] not in ('0','1','2') or not pair['reviewer'].strip() or not pair['reason'].strip():
            raise qa.EvaluationError('金标重复或尚未完成人工审核')
        if pair['stable_id'] not in segments or pair['content']!=segments[pair['stable_id']]['content'] or pair['source_document']!=segments[pair['stable_id']]['source_document']:
            raise qa.EvaluationError('金标正文与快照不一致')
        labels[key]=int(pair['relevance'])
    if set(labels)!=expected:raise qa.EvaluationError('必须审核完整有答案题×全量片段，不能只标注Top3')
    by_content={}
    for sid,s in segments.items():by_content.setdefault((s['source_document'],kb.normalize(s['content'])),[]).append(sid)
    mapping={}
    for row in rows:
        for fragment in row['fragments']:
            matches=by_content.get((fragment['source_document'],kb.normalize(fragment['content'])),[])
            if len(matches)!=1:raise qa.EvaluationError('历史返回片段与快照无法唯一映射；可能已重新分段，需稳定ID或同版本重跑')
            mapping[(row['id'],fragment['id'])]=matches[0]
    return labels,mapping,segments


def analyze(run_dir,reviews,out,corpus_path=None,qrels_dir=None):
    report=json.loads((run_dir/'results.json').read_text(encoding='utf-8'))
    rows=report['rows'];by_id={r['id']:r for r in rows}
    if report.get('backend')!='dify-document-kb' or len(rows)!=20 or any(r['status']!='ok' for r in rows):
        raise qa.EvaluationError('本次指标分析要求完整成功的文档20题；失败批次应先单独报告失败及缺失项')
    if reviews:
        manifest=json.loads((reviews/'manifest.json').read_text())
        if manifest['run_sha256']!=hashlib.sha256((run_dir/'results.json').read_bytes()).hexdigest():raise qa.EvaluationError('审核记录与运行文件不匹配')
        for name,h in manifest['review_files_sha256'].items():
            if hashlib.sha256((reviews/name).read_bytes()).hexdigest()!=h:raise qa.EvaluationError('审核文件哈希不符，请更新审核manifest')
    full=None
    if corpus_path or qrels_dir:
        if not corpus_path or not qrels_dir:raise qa.EvaluationError('严格Recall需要同时提供corpus和qrels目录')
        full=strict_qrels(corpus_path,qrels_dir,rows)
    review_map={}
    if reviews:
        for p in read_csv(reviews/'retrieval.csv'):
            key=(p['question_id'],int(p['rank']))
            if key in review_map or p['relevance'] not in ('0','1','2') or not p['reviewer'] or not p['reason']:raise qa.EvaluationError('返回片段审核缺失或重复')
            r=by_id[p['question_id']];f=r['fragments'][int(p['rank'])-1]
            if p['evidence_sha256']!=fingerprint(f['source_document'],f['content']):raise qa.EvaluationError('审核证据与返回正文不符')
            review_map[key]=int(p['relevance'])
        if set(review_map)!={(r['id'],i) for r in rows for i in range(1,len(r['fragments'])+1)}:
            raise qa.EvaluationError('返回片段必须逐项审核')
    per=[]
    for row in rows:
        labels=[];denom=None
        if row['answerable']:
            if full:
                qrels,mapping,_=full
                relevant={sid for (qid,sid),grade in qrels.items() if qid==row['id'] and grade>=2}
                if not relevant:raise qa.EvaluationError('有答案题没有相关金标片段：'+row['id'])
                ids=[mapping[(row['id'],f['id'])] for f in row['fragments'][:3]]
                if len(ids)!=len(set(ids)):raise qa.EvaluationError('返回片段映射到重复永久ID')
                labels=[qrels[(row['id'],sid)]>=2 for sid in ids];denom=len(relevant)
            elif review_map:
                labels=[review_map[(row['id'],i)]>=2 for i in range(1,len(row['fragments'][:3])+1)]
        first=next((i for i,v in enumerate(labels,1) if v),None)
        covered=row.get('evidence_groups_covered',[])
        per.append(dict(id=row['id'],answerable=row['answerable'],source_hit=row.get('source_hit'),
            evidence_all_hit=row.get('retrieval_hit'),evidence_group_coverage=(sum(covered)/len(covered) if covered else None),
            precision_at_3=sum(labels)/3 if labels or (row['answerable'] and (full or review_map)) else None,
            hit_at_3=bool(first) if row['answerable'] and (full or review_map) else None,
            reciprocal_rank_at_3=1/first if first else (0 if row['answerable'] and (full or review_map) else None),
            recall_at_3=sum(labels)/denom if denom else None,relevant_total=denom,
            total_seconds=row['total_seconds']))
    def avg(key):
        vals=[p[key] for p in per if p[key] is not None]
        return dict(value=statistics.mean(vals) if vals else None,evaluated=len(vals))
    times=sorted(r['total_seconds'] for r in rows)
    metrics=dict(source_run_sha256=hashlib.sha256((run_dir/'results.json').read_bytes()).hexdigest(),
        scope='complete_corpus_reviewed_qrels' if full else 'human_reviewed_returned_top3_only' if review_map else 'rules_and_existing_scores_only',
        review_attribution=json.loads((qrels_dir/'review-manifest.json').read_text()).get('reviewer_description') if full else (manifest.get('reviewer_description') if reviews else None),
        retrieval={k:avg(k) for k in ('precision_at_3','hit_at_3','reciprocal_rank_at_3','recall_at_3','evidence_group_coverage')},
        recall_status='available_from_complete_corpus_qrels' if full else 'pending_full_corpus_export_and_complete_qrels',
        answer_correct=dict(correct=sum(r['answer_correct'] is True for r in rows),reviewed=sum(r['answer_correct'] is not None for r in rows),total=15),
        refusal_correct=dict(correct=sum(r['refusal_correct'] is True for r in rows),reviewed=sum(r['refusal_correct'] is not None for r in rows),total=5),
        timing=dict(mean=statistics.mean(times),p95_nearest_rank=times[math.ceil(.95*len(times))-1],max=max(times),count=len(times)),
        success=dict(successful=len(rows),total=20),per_question=per)
    if full:
        metrics['corpus_review_manifest']=json.loads((qrels_dir/'review-manifest.json').read_text())
        metrics['corpus_sha256']=hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    if reviews:
        claims=read_csv(reviews/'claims.csv');groups=read_csv(reviews/'citation-groups.csv')
        claim_keys=set()
        for c in claims:
            key=(c['question_id'],c['claim_id'])
            if key in claim_keys or c['question_id'] not in by_id or c['supported'] not in ('true','false') or not c['reason'].strip() or not c['reviewer'].strip():
                raise qa.EvaluationError('事实审核不完整或重复')
            claim_keys.add(key)
            actual=set(by_id[c['question_id']]['cited_ids'])
            if not set(json.loads(c['supporting_citations']))<=actual:raise qa.EvaluationError('审核引用未出现在原答案')
        seen=set()
        for g in groups:
            q=g['question_id']
            if not g['reason'].strip() or not g['reviewer'].strip() or q in seen or q not in by_id or g['support'] not in ('full','partial','none') or set(json.loads(g['cited_ids']))!=set(by_id[q]['cited_ids']):raise qa.EvaluationError('引用组审核与原答案不符')
            seen.add(q)
        if seen!={r['id'] for r in rows if r.get('cited_ids')}:raise qa.EvaluationError('引用组未审核完整')
        coverage=manifest.get('claim_review_complete_question_ids',[])
        if set(coverage)!=set(by_id):raise qa.EvaluationError('必须明确审核全部问题，包括无事实断言的响应')
        supported=sum(c['supported']=='true' for c in claims)
        cited_supported=sum(c['supported']=='true' and bool(json.loads(c['supporting_citations'])) for c in claims)
        metrics['generation_audit']=dict(factual_claim_count=len(claims),supported_claim_count=supported,
            faithfulness=supported/len(claims) if claims else None,
            supported_and_cited_claims=cited_supported,citation_coverage=cited_supported/len(claims) if claims else None,
            citation_group_count=len(groups),fully_supported_groups=sum(g['support']=='full' for g in groups),
            partially_supported_groups=sum(g['support']=='partial' for g in groups),
            citation_group_full_support_rate=sum(g['support']=='full' for g in groups)/len(groups) if groups else None,
            no_factual_claim_ids=[r['id'] for r in rows if not any(c['question_id']==r['id'] for c in claims)],
            caution='事实与引用为助手事后语义审核，不是独立盲评；无事实断言不计作忠实度满分，拒答另评。')
    out.mkdir(parents=True,exist_ok=False)
    qa.write_json(out/'metrics.json',metrics)
    write_csv(out/'per-question.csv',per,list(per[0]))
    lines=['# 知识库指标审计','',f"审核范围：{metrics['scope']}",'',
           '| 指标 | 值 | 已评有答案题 |','|---|---|---|']
    for key,val in metrics['retrieval'].items():
        shown='待全量片段与金标' if val['value'] is None else f"{val['value']:.4f}"
        lines.append(f"| {key} | {shown} | {val['evaluated']} |")
    lines+=['',f"回答正确：{metrics['answer_correct']['correct']}/15；拒答正确：{metrics['refusal_correct']['correct']}/5（沿用用户评分）。",'',
            f"客户端平均 {metrics['timing']['mean']:.4f}秒；P95（nearest-rank）{metrics['timing']['p95_nearest_rank']:.4f}秒，样本20。",'',
            'Precision固定除以K=3；Hit/MRR在15道有答案题上宏平均。Recall只在完整片段快照且全部题片段组合审核后显示。', '',
            '证据组覆盖按已有短语金标计算，与Hit不是同一指标。无答案题不进入检索指标分母。']
    if full:
        lines+=['','全量相关性审核：'+str(metrics['review_attribution']),'',metrics['corpus_review_manifest'].get('snapshot_caveat','')]
    if 'generation_audit' in metrics:
        audit=metrics['generation_audit']
        lines+=['','## 事实与引用审核','',
            '| 指标 | 结果 |','|---|---|',
            f"| 事实忠实度 | {audit['supported_claim_count']}/{audit['factual_claim_count']} |",
            f"| 有支持且有引用的事实比例 | {audit['supported_and_cited_claims']}/{audit['factual_claim_count']} |",
            f"| 引用组完全支持率 | {audit['fully_supported_groups']}/{audit['citation_group_count']} |",
            '',audit['caution'],'',
            '以事实为单位的忠实度不等于以完整答案为单位的正确率。审核表保留原文、分级和理由，可逐项复核。']
    lines+=['','原始run和用户评分不修改；补充审核的作者、范围和局限见review manifest。']
    text='\n'.join(lines)+'\n';(out/'report.md').write_text(text,encoding='utf-8')
    page='<!doctype html><meta charset="utf-8"><title>知识库指标审计</title><style>body{font:16px system-ui;margin:32px}pre{white-space:pre-wrap}</style><h1>知识库指标审计</h1><pre>'+html.escape(text)+'</pre>'
    (out/'report.html').write_text(page,encoding='utf-8')
    print(text)
    return metrics


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    e=sub.add_parser('export');e.add_argument('--api-base',default='http://localhost/v1');e.add_argument('--dataset-id',action='append');e.add_argument('--output-dir',type=Path,required=True)
    q=sub.add_parser('prepare');q.add_argument('--corpus',type=Path,required=True);q.add_argument('--output-dir',type=Path,required=True)
    a=sub.add_parser('analyze');a.add_argument('--run-dir',type=Path,required=True);a.add_argument('--reviews',type=Path);a.add_argument('--corpus',type=Path);a.add_argument('--qrels-dir',type=Path);a.add_argument('--output-dir',type=Path,required=True)
    v=sub.add_parser('view');v.add_argument('--audit-dir',type=Path,required=True);v.add_argument('--open',action='store_true')
    args=p.parse_args()
    try:
        if args.cmd=='view':
            print((args.audit_dir/'report.md').read_text(encoding='utf-8'))
            if args.open:webbrowser.open((args.audit_dir/'report.html').resolve().as_uri())
        elif args.cmd=='export':
            key=os.environ.get('DIFY_DATASET_API_KEY') or getpass.getpass('知识库API Key（不是应用Key，输入不显示）：')
            if not key.strip() or '\r' in key or '\n' in key:raise qa.EvaluationError('需要有效的知识库API Key')
            client=de.Client(args.api_base,key.strip(),60);ids=args.dataset_id
            if not ids:
                datasets=page_all(client,'/datasets')
                for i,d in enumerate(datasets,1):print(i,d.get('name'),d.get('id'))
                numbers=input('选择包含这三份文档的知识库序号，多个用逗号分隔：')
                indices=[int(i.strip()) for i in numbers.split(',')]
                if not indices or any(i<1 or i>len(datasets) for i in indices):raise qa.EvaluationError('序号超出范围')
                ids=[datasets[i-1]['id'] for i in indices]
            if len(ids)!=len(set(ids)):raise qa.EvaluationError('知识库重复选择')
            export_corpus(client,ids,args.output_dir)
        elif args.cmd=='prepare':prepare(args.corpus,args.output_dir)
        else:analyze(args.run_dir,args.reviews,args.output_dir,args.corpus,args.qrels_dir)
        return 0
    except (qa.EvaluationError,OSError,ValueError,KeyError,IndexError) as error:
        print('错误：'+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
