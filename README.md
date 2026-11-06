# knowledge-qa-eval

项目已完成关键词检索、本机 Ollama 问答评测和 Dify Workflow API 批量评测三个阶段。所有 IT 服务台资料均为虚构。

当前进度和启动方式见 [Demo 交付说明](docs/demo-handoff.md)；第三阶段真实实测见 [Dify 评测归档](reports/dify-run-01/README.md)。

第二阶段使用原接口和原 20 题，固定 top_k=3，调用本机 qwen3:4b。安装、运行、评分及云端验证边界见 [Mac 问答评测说明](docs/phase2-mac.md)。推荐从 [统一运行与查看报告](docs/workflow.md) 开始。已完成的 [Mac 真实模型实测归档](reports/mac-run-02/README.md) 包含原始结果、人工评分和耗时。

```bash
source qa/bin/activate
python manage.py run
python manage.py review
python manage.py report --open
```

以下保留第一阶段说明。

## 环境和启动

需要 Python 3.10+；本次实际使用 Python 3.12。以下命令在仓库根目录运行。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

服务启动后，在另一个终端运行：

```bash
source .venv/bin/activate
python evaluate.py --base-url http://127.0.0.1:8765
```

脚本使用真实 HTTP 请求，先验证接口，再用同一份固定题集分别运行 k=3、k=5；任何接口状态、数据数量或排序断言失败会以非零状态退出。可用 `--output-dir reports/reproduced` 保存新结果而保留本次报告。无需联网调用模型。

## 接口

`POST /retrieval`，JSON 必填字段 `query`（去空白后非空，最长 2000 字符），可选 `top_k`（整数 1–12，默认 3）。返回 `query`、`top_k` 和 `results`；片段包含 `id`、`title`、`content`、`score`、`matched_keywords`。无匹配返回 HTTP 200 和空列表，缺参数或非法参数返回 HTTP 422。交互文档：`http://127.0.0.1:8765/docs`。

```bash
# 正常检索
curl --noproxy '*' -i http://127.0.0.1:8765/retrieval -H 'Content-Type: application/json' -d '{"query":"忘记密码怎么重置","top_k":3}'
# 无匹配：results=[]
curl --noproxy '*' -i http://127.0.0.1:8765/retrieval -H 'Content-Type: application/json' -d '{"query":"量子香蕉星际旅行","top_k":3}'
# 缺少必填 query：422
curl --noproxy '*' -i http://127.0.0.1:8765/retrieval -H 'Content-Type: application/json' -d '{"top_k":3}'
```

## 检索规则与评测口径

`data/corpus.json` 包含 12 个固定片段及人工关键词。对查询与关键词做 Unicode NFKC 规范化并忽略大小写；关键词在查询中出现则计 1 分，同一关键词仅计一次。只返回正分片段，按分数降序、ID 升序排序，截取前 k 条，不补齐零分片段。此基线使用子串匹配，不做分词、词边界判断或语义推理，可能存在词内误匹配。

`data/questions.json` 包含固定 20 题，15 题有答案并标注正确片段 ID，5 题无答案且正确 ID 列表为空。检索服务不读取测试题和答案标签。

有答案题的正确 ID 出现在返回列表中即计命中（Hit@k，分母 15）。无答案题单独统计返回非空列表的题数（分母 5），不混入命中率。报告保存题集和语料 SHA256，便于核实两组使用相同数据。

## 本次实跑

| top_k | 命中 | 漏题 | 无答案仍返回 |
|---|---|---|---|
| 3 | 14/15（93.3%） | Q15 | 3/5（Q16、Q17、Q18） |
| 5 | 14/15（93.3%） | Q15 | 3/5（Q16、Q17、Q18） |

Q15“文档不小心扔掉了怎样找回？”的正确片段为 IT-012，但关键词未覆盖这些同义表达。Q16–Q18 分别询问 VPN 出口国家、邮箱单封发送限制、打印收费，相关资料没有给出答案，却因主题关键词匹配而返回片段。增加 k 无法修复这两种问题。

- `reports/report.md`：汇总、逐题 ID 与判定。
- `reports/results.csv`：40 行逐题结果，含题目、标注 ID、返回 ID、命中和误返回。
- `reports/results.json`：完整原始 HTTP 验证请求/响应及全部 40 次检索响应（含分数和关键词）、运行时间、数据哈希。

执行环境最初禁止创建 socket；本次在获得执行权限后实际启动 Uvicorn，并使用 HTTP 访问本机 8765 端口完成验证，并非直接调用函数或 TestClient。本题集仅用于小规模可复现基线，不能外推真实业务准确率。

## Dify 参数诊断

若 Dify 已设置 think=false 但仍出现思考或速度异常，可用 [本地 Ollama 参数诊断转发器](docs/ollama-probe.md) 检查实际 HTTP 请求，无需重新安装环境。

## Dify 与本地配置对齐

直接导入 [对齐工作流包](dify/knowledge-qa-eval-aligned.ifpkg)，按 [配置与验证说明](docs/dify-alignment.md) 进行单题测试。维护者运行完整测试前安装 requirements-dev.txt。

## 第三阶段 Dify API 批量评测

使用 [dify_eval.py](dify_eval.py) 调用已发布工作流，沿用原20题和人工评分，参见 [Mac运行、密钥及耗时说明](docs/dify-evaluation.md)。

第三阶段已在用户 Mac 上完成20题真实API调用及人工评分：检索14/15、回答13/15、正确拒答1/5。两阶段逐题片段和答案20/20相同，详见 [跨阶段对比](reports/dify-run-01/comparison.json)。
