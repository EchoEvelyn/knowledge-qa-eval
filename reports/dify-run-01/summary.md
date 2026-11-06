# 第三阶段 Dify 问答评测

模型：qwen3:4b；top_k=3；状态：completed；处理 20/20 题。

预检错误：无

已确认检索命中：14，完整题集分母为 15；失败题：[]。

语义评分为人工判定。null/空白表示待评，不代表错误或正确。模型 insufficient 只是自述。

- answer_correct：正确 13，已评 15/15，已生成待评 []，未处理 0
- refusal_correct：正确 1，已评 5/5，已生成待评 []，未处理 0

详见 results.csv 和 results.json；命中率分母固定为 15，完整正确率仅在完成 20 题且对应字段全部评分后报告。

调用方式：已发布的 Dify Workflow API（blocking），非脚本直接调用检索或 Ollama。
total_seconds 是客户端请求及本地校验耗时；workflow_elapsed_seconds 是 Dify 工作流耗时。API 未提供检索/模型分段耗时，retrieval_seconds 和 generation_seconds 留空。
model/top_k 为本轮预期配置，API 不直接证明内部模型参数；应以已发布工作流和诊断记录核对。
