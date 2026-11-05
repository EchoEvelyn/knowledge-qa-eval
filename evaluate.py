"""Evaluate the actual HTTP service; uses only Python standard library."""
import argparse
import csv
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent
# Loopback service requests must not be sent through the outbound HTTP proxy.
CLIENT = build_opener(ProxyHandler({}))


def post(base_url, payload):
    request = Request(base_url.rstrip('/') + '/retrieval',
                      data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        response = CLIENT.open(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def run(base_url, output):
    output.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((ROOT / 'data/corpus.json').read_text())
    cases = json.loads((ROOT / 'data/questions.json').read_text())
    known = {doc['id'] for doc in corpus}
    assert len(corpus) == len(known) == 12
    assert len(cases) == len({c['id'] for c in cases}) == 20
    assert sum(c['answerable'] for c in cases) == 15
    assert all(bool(c['expected_ids']) == c['answerable'] for c in cases)
    assert all(set(c['expected_ids']) <= known for c in cases)
    checks = []
    for name, payload, expected in [
        ('正常检索', {'query': '忘记密码怎么重置', 'top_k': 3}, 200),
        ('无匹配', {'query': '量子香蕉星际旅行', 'top_k': 3}, 200),
        ('缺少必填参数', {'top_k': 3}, 422),
        ('空白问题', {'query': '   '}, 422),
        ('非法top_k', {'query': '密码', 'top_k': 0}, 422),
    ]:
        status, body = post(base_url, payload)
        checks.append(dict(name=name, request=payload, status=status, response=body))
        assert status == expected, checks[-1]
        if name == '正常检索':
            assert body['results'][0]['id'] == 'IT-001'
        if name == '无匹配':
            assert body['results'] == []
    rows, summaries = [], []
    for k in (3, 5):
        current = []
        for case in cases:
            status, body = post(base_url, dict(query=case['query'], top_k=k))
            assert status == 200, (case, status, body)
            results = body['results']
            ids = [r['id'] for r in results]
            assert len(ids) <= k and len(ids) == len(set(ids))
            assert set(ids) <= known and all(r['score'] > 0 for r in results)
            assert results == sorted(results, key=lambda r: (-r['score'], r['id']))
            row = dict(**case, top_k=k, returned_ids=ids,
                       hit=bool(set(ids) & set(case['expected_ids'])) if case['answerable'] else None,
                       false_return=bool(ids) if not case['answerable'] else None,
                       response=body)
            current.append(row)
        summaries.append(dict(top_k=k, answerable=15,
                              hits=sum(r['hit'] is True for r in current),
                              missed_ids=[r['id'] for r in current if r['hit'] is False],
                              unanswerable=5,
                              false_returns=sum(r['false_return'] is True for r in current),
                              false_return_ids=[r['id'] for r in current if r['false_return'] is True]))
        rows.extend(current)
    for case in cases:
        a, b = [r for r in rows if r['id'] == case['id']]
        assert b['returned_ids'][:3] == a['returned_ids']
    report = dict(run_at_utc=datetime.now(timezone.utc).isoformat(),
                  base_url=base_url, python=platform.python_version(),
                  data_sha256={name: hashlib.sha256((ROOT / 'data' / name).read_bytes()).hexdigest()
                               for name in ('corpus.json', 'questions.json')},
                  http_checks=checks, summaries=summaries, rows=rows)
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    with (output / 'results.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['top_k','题号','问题','有答案','正确片段ID','返回片段ID（排名顺序）','命中','无答案却返回'])
        for r in rows:
            writer.writerow([r['top_k'],r['id'],r['query'],r['answerable'],','.join(r['expected_ids']),
                             ','.join(r['returned_ids']),r['hit'],r['false_return']])
    lines = ['# 第一阶段实际 HTTP 评测报告', '', f"运行时间（UTC）：{report['run_at_utc']}", '',
             '命中定义：15 道有答案题中，返回前 k 个片段包含标注 ID 即命中。无答案题单独统计是否返回任意片段；不计入命中率分母。', '',
             '| top_k | 命中/有答案题 | 漏题 | 无答案仍返回/无答案题 | 误返回题号 |',
             '|---|---|---|---|---|']
    for s in summaries:
        lines.append(f"| {s['top_k']} | {s['hits']}/15 | {', '.join(s['missed_ids']) or '无'} | {s['false_returns']}/5 | {', '.join(s['false_return_ids']) or '无'} |")
    lines += ['', '## HTTP 验证', '', '| 场景 | HTTP 状态 |', '|---|---|']
    lines += [f"| {c['name']} | {c['status']} |" for c in checks]
    lines += ['', '完整请求和响应保存在 results.json。', '', '## 逐题结果', '',
              '| 题号 | 问题 | 正确ID | k=3返回（按排名） | k=5返回（按排名） | k=3/k=5判定 |',
              '|---|---|---|---|---|---|']
    for case in cases:
        a, b = [r for r in rows if r['id'] == case['id']]
        def verdict(r):
            return ('命中' if r['hit'] else '漏检') if r['answerable'] else ('误返回' if r['false_return'] else '正确空返回')
        lines.append(f"| {case['id']} | {case['query']} | {','.join(case['expected_ids']) or '无答案'} | {','.join(a['returned_ids']) or '空'} | {','.join(b['returned_ids']) or '空'} | {verdict(a)}/{verdict(b)} |")
    lines += ['', '## 解释与限制', '',
              'Q15 使用“文档、扔掉、找回”等未收录的同义表达，关键词基线无法召回 IT-012。无答案题中的 VPN、邮箱、打印机关键词会触发相关片段，但资料并未回答所问的具体限制或费用。增加 top_k 无法修复零关键词匹配，也不能判断问题是否有答案。', '',
              '本结果只反映固定虚构题集的检索表现，不代表真实业务准确率；未接入生成模型、Dify 或 Ollama。']
    (output / 'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'reports')
    args = parser.parse_args()
    run(args.base_url, args.output_dir)
