"""Validate generated artifacts and execute pure node code outside Dify."""
import hashlib
import json
import unittest
import zipfile
from pathlib import Path

import yaml

import qa_eval as qa
from tools.build_dify_alignment import build, IDS


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.app=build()
        self.nodes={n['id']:n['data'] for n in self.app['workflow']['graph']['nodes']}

    def invoke(self, node, **values):
        scope={}
        exec(self.nodes[IDS[node]]['code'],scope)
        return scope['main'](**values)

    def test_prompt_matches_local_for_all_20_archived_cases(self):
        baseline=json.loads((qa.ROOT/'reports/mac-run-02/results.json').read_text())
        for row in baseline['rows']:
            with self.subTest(question=row['id']):
                result=self.invoke('prepare',query=row['query'],body=json.dumps({'results':row['fragments']}),status_code=200)
                self.assertEqual(result['user_prompt'],qa.messages(row['query'],row['fragments'])[1]['content'])
        self.assertEqual(self.nodes[IDS['llm']]['prompt_template'][0]['text'],qa.SYSTEM_PROMPT)

    def test_dynamic_query_handles_quotes_and_newlines(self):
        query='带有"引号"\n和换行的问题'
        result=self.invoke('request',query=query)
        self.assertEqual(json.loads(result['request_body']),{'query':query,'top_k':3})
        self.assertEqual(self.nodes[IDS['http']]['body']['data'][0]['value'], '{{#'+IDS['request']+'.request_body#}}')

    def test_schema_parameters_and_output_parser(self):
        params=self.nodes[IDS['llm']]['model']['completion_params']
        self.assertEqual(json.loads(params['json_schema']),qa.ANSWER_SCHEMA)
        self.assertEqual({k:v for k,v in params.items() if k!='json_schema'},
                         dict(temperature=0,seed=42,num_predict=2048,think=False))
        for insufficient in (True,False):
            raw=json.dumps(dict(answer='测试 [IT-001]',insufficient=insufficient))
            result=self.invoke('parse',text=raw)
            self.assertIs(result['insufficient'],insufficient)
            self.assertEqual(result['raw_answer'],raw)
        with self.assertRaises(ValueError):
            self.invoke('parse',text='{"question":"重复输入"}')
        with self.assertRaises(ValueError):
            self.invoke('parse',text='{"answer":"答案", "insufficient":"false"}')
        with self.assertRaises(ValueError):
            self.invoke('prepare',query='x',body='{}',status_code=500)

    def test_package_integrity_and_generated_yaml_up_to_date(self):
        path=qa.ROOT/'dify/knowledge-qa-eval-aligned.ifpkg'
        with zipfile.ZipFile(path) as package:
            self.assertEqual(set(package.namelist()),{'manifest.yaml','app.yaml'})
            manifest=yaml.safe_load(package.read('manifest.yaml'))
            body=package.read('app.yaml')
            self.assertEqual(manifest['apps'][0]['sha256'],hashlib.sha256(body).hexdigest())
            self.assertEqual(manifest['apps'][0]['size'],len(body))
            self.assertEqual(body,(qa.ROOT/'dify/knowledge-qa-eval-aligned.yaml').read_bytes())
            self.assertEqual(yaml.safe_load(body),self.app)
        order=list(IDS.values())
        self.assertEqual([(e['source'],e['target']) for e in self.app['workflow']['graph']['edges']],list(zip(order,order[1:])))
        self.assertEqual(self.app['workflow']['environment_variables'],[])


if __name__=='__main__':
    unittest.main()
