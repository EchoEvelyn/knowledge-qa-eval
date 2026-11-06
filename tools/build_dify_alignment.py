"""Build importable Dify artifacts from qa_eval's authoritative prompt/schema.

Development dependency: PyYAML. Mac users only need to import the generated file.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qa_eval as qa

IDS = dict(start='1793933305464', request='1794000000001', http='1793933606269',
           prepare='1794000000002', llm='1793934553252', parse='1794000000003', end='1793934791127')


def ref(node, variable):
    return '{{#' + IDS[node] + '.' + variable + '#}}'


def code_node(key, title, code, inputs, outputs):
    return dict(title=title, type='code', code_language='python3', code=code,
                variables=[dict(variable=name, value_selector=[IDS[source], field], value_type=kind)
                           for name, source, field, kind in inputs],
                outputs={name: dict(type=kind, children=None) for name, kind in outputs.items()})


def build():
    prefix = qa.messages('', [])[1]['content'].split('\n', 1)[0] + '\n'
    request_code = '''import json

def main(query: str) -> dict:
    return {"request_body": json.dumps({"query": query, "top_k": 3}, ensure_ascii=False)}
'''
    prepare_code = '''import json

def main(query: str, body: str, status_code: int) -> dict:
    if status_code != 200:
        raise ValueError("Retrieval HTTP request failed")
    fragments = json.loads(body)["results"]
    if not isinstance(fragments, list) or len(fragments) > 3:
        raise ValueError("Invalid retrieval results")
    context = [{key: fragment[key] for key in ("id", "title", "content")} for fragment in fragments]
    return {"user_prompt": PREFIX + json.dumps({"question": query, "fragments": context}, ensure_ascii=False)}
'''.replace('PREFIX', repr(prefix))
    parse_code = '''import json

def main(text: str) -> dict:
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"answer", "insufficient"}:
        raise ValueError("Expected answer and insufficient only")
    if not isinstance(value["answer"], str) or not value["answer"].strip():
        raise ValueError("Missing nonempty answer")
    if type(value["insufficient"]) is not bool:
        raise ValueError("insufficient must be boolean")
    return {"answer": value["answer"], "insufficient": value["insufficient"], "raw_answer": text}
'''
    data = {
        'start': dict(title='User Input', type='start', variables=[dict(variable='query', label='问题',
                       type='text-input', required=True, max_length=2000, default='', options=[], hint='')]),
        'request': code_node('request', '构造检索请求', request_code,
                            [('query','start','query','string')], {'request_body':'string'}),
        'http': dict(title='HTTP Request', type='http-request', method='post',
                     url='http://host.docker.internal:8765/retrieval',
                     authorization=dict(type='no-auth', config=None), headers='Content-Type:application/json',
                     params='', variables=[], ssl_verify=True,
                     body=dict(type='json', data=[dict(id='query-json',key='',type='text',value=ref('request','request_body'))]),
                     timeout=dict(max_connect_timeout=10,max_read_timeout=10,max_write_timeout=10),
                     retry_config=dict(retry_enabled=False,max_retries=3,retry_interval=100)),
        'prepare': code_node('prepare', '组装与本地一致的提示', prepare_code,
                            [('query','start','query','string'),('body','http','body','string'),
                             ('status_code','http','status_code','number')], {'user_prompt':'string'}),
        'llm': dict(title='LLM', type='llm', context=dict(enabled=False,variable_selector=[]),
                    model=dict(provider='langgenius/ollama/ollama', name='\tqwen3:4b', mode='chat',
                               completion_params=dict(temperature=0,seed=42,num_predict=2048,think=False,
                                   json_schema=json.dumps(qa.ANSWER_SCHEMA,ensure_ascii=False))),
                    prompt_template=[dict(id='aligned-system',role='system',text=qa.SYSTEM_PROMPT),
                                     dict(id='aligned-user',role='user',text=ref('prepare','user_prompt'))],
                    reasoning_format='separated',vision=dict(enabled=False)),
        'parse': code_node('parse', '解析模型 JSON', parse_code,
                          [('text','llm','text','string')],
                          {'answer':'string','insufficient':'boolean','raw_answer':'string'}),
        'end': dict(title='Output',type='end',outputs=[
            dict(variable=name,value_selector=[IDS[source],field],value_type=kind)
            for name,source,field,kind in [('answer','parse','answer','string'),
                ('insufficient','parse','insufficient','boolean'),('raw_answer','parse','raw_answer','string'),
                ('retrieval_body','http','body','string')]])}
    order = list(IDS)
    nodes = [dict(id=IDS[key],type='custom',width=242,height=110,
                  position=dict(x=80+310*i,y=282),positionAbsolute=dict(x=80+310*i,y=282),
                  sourcePosition='right',targetPosition='left',selected=False,
                  data=dict(**data[key],selected=False)) for i,key in enumerate(order)]
    edges = [dict(id=f'{IDS[a]}-{IDS[b]}',source=IDS[a],target=IDS[b],sourceHandle='source',
                  targetHandle='target',type='custom',data=dict(sourceType=data[a]['type'],
                    targetType=data[b]['type'],isInIteration=False,isInLoop=False)) for a,b in zip(order,order[1:])]
    return dict(version='0.7.0',kind='app',app=dict(name='knowledge-qa-eval-aligned',mode='workflow',
                description='与 qa_eval.py 对齐的提示词、资料和 JSON Schema；保留旧工作流用于对照',
                icon='🤖',icon_type='emoji',icon_background='#FFEAD5',use_icon_as_answer_icon=False),
                dependencies=[dict(type='marketplace',current_identifier=None,value=dict(version=None,
                    marketplace_plugin_unique_identifier='langgenius/ollama:1.0.3@1086758302d98263d5b7881de8ba95fc219351fdf97831d18a7807378674d203'))],
                workflow=dict(conversation_variables=[],environment_variables=[],rag_pipeline_variables=[],
                    features=dict(file_upload={},retriever_resource=dict(enabled=True),
                                  sensitive_word_avoidance=dict(enabled=False),
                                  text_to_speech=dict(enabled=False,language='',voice='')),
                    graph=dict(nodes=nodes,edges=edges,viewport=dict(x=0,y=0,zoom=.65))))


def main():
    out=ROOT/'dify'
    out.mkdir(exist_ok=True)
    body=yaml.safe_dump(build(),allow_unicode=True,sort_keys=False).encode()
    (out/'knowledge-qa-eval-aligned.yaml').write_bytes(body)
    manifest=yaml.safe_dump(dict(format='dify.app',format_version=2,apps=[dict(path='app.yaml',
                  sha256=hashlib.sha256(body).hexdigest(),size=len(body))]),sort_keys=False).encode()
    with zipfile.ZipFile(out/'knowledge-qa-eval-aligned.ifpkg','w',zipfile.ZIP_DEFLATED) as package:
        for name,content in [('manifest.yaml',manifest),('app.yaml',body)]:
            info=zipfile.ZipInfo(name,date_time=(2026,11,6,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            package.writestr(info,content)
    print('Generated YAML and .ifpkg in',out)


if __name__=='__main__':
    main()
