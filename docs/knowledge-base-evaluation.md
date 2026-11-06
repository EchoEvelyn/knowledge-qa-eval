# 第四阶段：三份文档知识库的独立批量评测

本阶段从已经跑通的单题节点测试继续，不重跑旧JSON资料的20题。新增题集为D01–D20（15有答案、5无答案），覆盖三份TXT；不与原Q01–Q20合并报告。

## 先在Dify完成两件事

1. 确认新文档应用knowledge-qa-docs的Knowledge Retrieval能够查询三份文档所在的全部知识库，而不是只选文件恢复文档的知识库。
2. 发布该应用当前版本，在该应用的Access Point → Backend Service API创建API Key。使用新应用密钥，不是旧aligned应用的密钥。

保持Docker Dify和Ollama运行，Ollama凭据直连11434。新链路不需要FastAPI 8765或诊断器11435。Output应保留answer、insufficient、raw_answer和retrieval_body；后者包含当前query、top_k=3及每个片段的KB编号、来源文件名title、content。

## Mac更新（不用重装环境）

推荐下载 [评测工具包](../knowledge-base/kb-evaluation-kit.zip)，包含kb_eval.py、已有通用模块、独立题集、三份TXT和本说明，不含密钥和新向量索引。

包内脚本放项目根目录，knowledge-base和docs按目录结构合并。Mac可直接使用系统ditto解压到原项目目录；它保留其他文件和旧reports，不需要创建新Python环境：

```bash
ditto -x -k ~/Downloads/kb-evaluation-kit.zip /Users/echo/Desktop/knowledge-qa-eval-main
cd /Users/echo/Desktop/knowledge-qa-eval-main
```

如果下载路径或文件名不同，调整ZIP路径。此工具包的题集和参考答案只给评测脚本读取，**不要上传到Dify知识库**。

## 先试D01一题

```bash
python3 kb_eval.py run --smoke
```

按提示输入新知识库应用的API Key，输入不会显示或保存；也可读取已设置的DIFY_API_KEY环境变量，确保不是旧应用密钥。

D01问“密码重置的短信验证码多久有效？”，应在账号文档找回“5分钟”。终端显示：

- source_hit：是否返回标注的正确来源文档。
- evidence_hit：是否在正确文档的返回正文中覆盖所需证据。
- 回答及耗时。

若工程为ok但source/evidence为False，先检查知识检索是否包含账号文档和正确知识库，不能将ok理解为检索正确。

默认结果在reports/phase4/时间戳/，单题明确标记为smoke。这里没有预先填入任何真实模型结果。

## 正式20题、评分、报告

单题确认后执行：

```bash
python3 kb_eval.py run --output-dir reports/kb-run-01
python3 kb_eval.py review --run-dir reports/kb-run-01
python3 kb_eval.py report --run-dir reports/kb-run-01 --open
```

目录必须是新的；原reports/mac-run-02、reports/dify-run-01保留。默认每题超时300秒，串行执行，不自动重试；超时不代表Dify已取消后台任务。API预检失败不会继续调用模型，生成中途错误会保存并继续，鉴权失效停止后续请求。

人工评分同样用y/n/s/q，不再向模型发送参考答案。答案正确性和无答案正确拒答均先为null，人工确认后才有得分。Dify HTTP成功但工作流status=failed时仍为错误。

## 金标与检索口径

题集文件 [questions.json](../knowledge-base/eval/questions.json) 包含：问题、是否有答案、预期来源文档、章节、参考答案、评分标准和证据短语组。

KB-1、KB-2是每次请求的引用别名，不能跨题作为金标。因此：

- source_hit只要求返回正确来源文档，不代表找到正确答案。
- retrieval_hit要求正确来源文档的返回正文合并后覆盖**所有证据组**；每组命中任一短语即可。只做NFKC、大小写、空白规范化，不做语义判定。
- 证据规则是可解释的检索辅助指标，可能因切分、改写、抽取差异产生误差。人工回答评分仍需阅读实际片段和参考标准，不能用规则命中自动判回答正确。
- 无答案题的source_hit/retrieval_hit为null，单独统计是否仍返回片段及人工拒答结果。

每个返回片段另记录来源文件名和“文件名+正文”的SHA256，用于回查本次证据；正文变化或重新分段会改变哈希，它不等于Dify永久segment_id。当前工作流未输出原segment_id；若以后加入，可在dify_response中保留，不能宣称已实现完整文档权限与版本治理。

results.json中的expected_ids只为复用人工评分引用检查，按**本次返回中属于标注来源文档的别名**生成，字段provenance明确记录其派生性质。真正金标是expected_sources/expected_sections/evidence_groups，不是expected_ids。

## 结果与时间

结果目录保存results.json、results.csv、summary.md、report.html，原始Dify请求/响应、每题run ID、资料、答案、引用和人工理由均保留。CSV保留workflow_run_id、服务端工作流耗时和total_tokens；JSON还包含证据组覆盖、source_hit、片段哈希及题集/文档/代码哈希。

客户端total_seconds、Dify workflow_elapsed_seconds的口径与第三阶段一致；API未提供的检索/模型分段耗时留空。不能把Dify total_tokens当作Ollama输出token，也不能把新旧不同语料的时间差当作单纯编排开销。

report离线生成不需要API Key，不修改原始JSON。归档目录不覆盖评分。Dify中的新知识库API和真实模型批量结果仍需Mac运行确认；仓库测试桩产生的合成结果不作为准确率证据。

## 当前工程验证

题集20道证据标注均已与本地TXT核对。工程测试检查：新旧数据集分离、仅命中文档不算证据命中、引用越界、错误query/缺来源检测、20题HTTP调用、评分复用、失败保留、密钥脱敏，以及旧基线不被改动。
