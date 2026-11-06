# knowledge-qa-eval

一个在 Mac 本地搭建的中文知识问答与评测 Demo：从 FastAPI 关键词检索，逐步接入 Ollama、Dify 工作流/API，再扩展到自有文档的 Embedding 向量知识库。重点是端到端流程可运行、可保存、可评分和可复现；资料均为虚构测试内容。

## 当前进展

| 阶段 | 内容 | 已验证范围 |
|---|---|---|
| 1. 检索基线 | FastAPI + 12条JSON资料 + 20题，top_k=3/5 | HTTP验证与两组检索评测完成 |
| 2. 本地问答 | Python直接调用检索和Ollama qwen3:4b | Mac真实20题生成、人工评分及报告完成 |
| 3. Dify应用/API | 可导入的对齐工作流、动态query、发布API | Mac真实20题API评测、评分和归档完成 |
| 4. 文档向量知识库 | 三份TXT入库、bge-m3、Knowledge Retrieval | Mac真实新20题API执行、人工评分和归档完成 |

新增：[知识库测评指标与完整Recall流程](docs/knowledge-base-metrics.md)；[现有20题指标审计](reports/kb-run-01-metric-audit/report.md)；[Mac指标工具包](knowledge-base/kb-metrics-kit.zip)。严格Recall等待全量片段与金标，其余指标已按现有记录补算。

完整架构、文件说明与当前边界见 [项目总览](docs/project-overview.md)。

## 两条现有链路

**已归档的固定题集基线：**

```text
原20题 → dify_eval.py → Dify aligned工作流
→ FastAPI /retrieval → 12条JSON资料 → Ollama → 保存/评分/报告
```

**最新跑通的文档知识库：**

```text
三份TXT → Dify解析分段 → bge-m3向量化 → Dify知识库
用户query → Knowledge Retrieval → 组装提示 → qwen3:4b
→ 解析模型JSON → Output
```

文档知识库路径不调用原FastAPI检索接口；8765服务仅在运行旧基线时需要。原应用和评测结果继续保留。

## 实测结果与证据

| 指标 | 第二阶段本地直连 | 第三阶段Dify API |
|---|---|---|
| 工程运行 | 20/20成功 | 20/20成功 |
| 检索命中 | 14/15 | 14/15 |
| 有答案题回答正确 | 13/15 | 13/15 |
| 无答案题正确拒答 | 1/5 | 1/5 |
| 客户端每题平均耗时 | 2.33秒 | 2.81秒 |

两阶段20题的片段、答案文本和评分逐项相同。不同日期运行的均值差不等同于纯Dify编排开销。

- [第二阶段Mac原始结果](reports/mac-run-02/README.md)
- [第三阶段Dify原始结果与对比](reports/dify-run-01/README.md)
- [第四阶段新文档20题实测](reports/kb-run-01/README.md)：来源15/15、证据14/15、回答13/15、拒答3/5，客户端平均6.97秒
- [早期单题验证记录](reports/knowledge-base-smoke/README.md)（历史记录）

原20题与正确片段标签仅针对12条JSON资料，不用于直接评价新文档知识库。第四阶段已使用独立D01–D20完成批量评测；新旧题集与命中定义不同，不直接据分数差判断效果提升。

## 从哪里开始

| 目的 | 文档/入口 |
|---|---|
| 查看全项目结构和进展 | [项目总览](docs/project-overview.md) |
| 运行原基线、了解端口 | [Demo交付与启动](docs/demo-handoff.md) |
| 用Python直接调用本机模型 | [统一运行说明](docs/workflow.md)，`python manage.py run` |
| 通过已发布Dify API评测 | [Dify评测指南](docs/dify-evaluation.md)，`python3 dify_eval.py run` |
| 导入固定JSON检索的Dify应用 | [对齐工作流说明](docs/dify-alignment.md) |
| 上传自有测试文档、接向量检索 | [文档知识库配置](docs/document-knowledge-base.md) |
| 下载三份测试材料 | [仅含TXT的ZIP](knowledge-base/it-demo-materials.zip) |
| 查看第一阶段接口和检索规则 | [检索基线说明](docs/retrieval-baseline.md) |
| 临时诊断Ollama请求 | [诊断工具说明](docs/ollama-probe.md) |

## 核心目录

```text
app.py                     FastAPI关键词检索服务
qa_eval.py                 本地Ollama问答评测与人工评分
manage.py                  本地运行、预检及HTML报告入口
dify_eval.py               已发布Dify aligned工作流的20题评测
ollama_probe.py            临时诊断转发器，正常运行无需启动
data/                      原12条资料、20题及评分标准
dify/                      第三阶段aligned应用的YAML和ifpkg
knowledge-base/             三份虚构文档，TXT上传版及Markdown源文件
reports/                   实测原始证据、评分、对比及工程测试日志
docs/                      操作、架构和交付说明
tools/                     对齐工作流生成器
tests/                     接口、流程、产物和归档检查
```

新文档批量评测请使用 [kb_eval.py与独立题集](docs/knowledge-base-evaluation.md)，不沿用旧dify_eval.py入口。

当前文档知识库工作流在用户Mac的Dify中手动配置；尚未导出到本仓库。`dify/knowledge-qa-eval-aligned.ifpkg` 仍是旧JSON基线应用，不是新文档知识库应用。

## 环境和工程检查

运行环境：Python 3.10+、Mac Ollama；Dify分支需要Docker Desktop。首次创建环境，已有qa环境只需激活：

```bash
python3 -m venv qa
source qa/bin/activate
python -m pip install -r requirements.txt
```

维护者运行包含工作流产物校验的完整工程检查：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

API密钥在本地隐藏输入，不写入代码或仓库。项目是单机测试Demo，尚未接入真实企业数据、文档权限过滤、持续同步或生产并发部署。
