# 第三阶段：Dify真实API评测交付

已完成“固定题集 → 已发布 Dify Workflow API → 本机检索 → 本机 Ollama → 保存 → 人工评分 → 报告”的完整闭环。本阶段验收以流程可运行、可保存、可评分和可复现为主，不针对准确率优化。

## 原始证据

本目录 results.json、results.csv、summary.md 为用户上传的 Mac 实跑文件，按原字节归档；provenance.json 记录文件哈希和核对项。report.html 是派生阅读版，comparison.json 是从两阶段逐题文件计算的对比。人工评分由用户在助手逐题建议下完成，不是独立盲评。

云端负责核对与归档，没有连接用户的 Dify 或重跑真实模型。数据哈希与仓库核对一致；客户端代码哈希与评测时提交 `631f000` 核对一致。API 中的模型字段是预期配置，不能凭它单独证明每次内部调用的模型版本；配置依据是已发布对齐工作流和此前诊断。

## 运行信息

- 时间（北京时间）：2026-11-06 14:21:14 至 14:22:10。
- 平台：macOS-26.6.2-arm64-arm-64bit；Python 3.11.9。
- 应用：knowledge-qa-eval-aligned；API：http://localhost/v1；请求方式：blocking。
- 工作流 ID：`4e0dd222-2bce-442f-b3e1-84bcfe89f054`；20 题分别保留唯一 workflow_run_id。
- 固定题集20题，资料12条，top_k=3；预期模型为 qwen3:4b，沿用本地 Schema 和提示词。

## 两阶段结果

| 指标 | 第二阶段直接调用 | 第三阶段Dify API |
|---|---|---|
| 程序成功 | 20/20 | 20/20 |
| 检索命中 | 14/15 | 14/15 |
| 有答案题回答正确 | 13/15 | 13/15 |
| 无答案题正确拒答 | 1/5 | 1/5 |
| 人工评分完成 | 20/20 | 20/20 |

逐题比较：返回片段 20/20 相同，答案文本 20/20 相同，语义判定 20/20 相同。并非只比较汇总分数。

## 耗时观察

| 测量项（秒） | 每题平均 | 每题中位数 | 最慢题 | 20题累计 |
|---|---|---|---|---|
| 第二阶段客户端 | 2.3327 | 2.6353 | 3.5326 | 46.6548 |
| 第三阶段客户端 | 2.8125 | 3.1766 | 4.1911 | 56.2504 |
| Dify服务端工作流 | 2.7565 | 3.1263 | 4.1332 | 55.1301 |

两轮客户端每题均值差约 0.48 秒。它是不同日期两次运行的观察差异，不等同于纯 Dify 开销；不能从中隔离模型速度、缓存、系统资源或流式方式的影响。

第三阶段 API 未提供节点耗时，retrieval_seconds 和 generation_seconds 保留 null；工作流 elapsed_time 不能当作模型生成时间。Dify total_tokens 也不能与 Ollama eval_count 直接比较。节点细节可根据 run ID 在 Dify Logs/TRACING 查询。

## 已知内容质量问题

- Q09 审批角色表述错误；Q15 检索为空并重复问题。
- Q16 正确拒答；Q17–Q20 重复问题，未明确资料不足。
- 这些结果与第二阶段一致，已保留为基线，不阻塞工程闭环验收。

## 查看与复现

```bash
python3 dify_eval.py report --run-dir reports/dify-run-01 --open
```

原始归档有 provenance.json 标识，不修改既有评分。需要新一轮时使用不同目录：

```bash
python3 dify_eval.py run --output-dir reports/dify-run-02
python3 dify_eval.py review --run-dir reports/dify-run-02
python3 dify_eval.py report --run-dir reports/dify-run-02 --open
```

保持 Docker Dify、Ollama 11434、检索服务 0.0.0.0:8765 运行；诊断转发器11435不需要运行。详见 [Dify评测说明](../../docs/dify-evaluation.md) 和 [Demo交付说明](../../docs/demo-handoff.md)。
