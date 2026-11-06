# 与本地脚本对齐的 Dify 工作流

## 本次解决什么

已有观测：同一道 Q01，本地生成 48 tokens、生成约 1.59 秒，Dify 原工作流生成 1047 tokens、生成约 32.99 秒，两者约 30–32 tokens/秒。观察支持“生成量不同是主要耗时来源”，但旧流程提示词和输出格式不同，不能当作严格性能对比。

本次不优化准确率，不修改 Ollama 或检索策略。生成文件直接引用 qa_eval.py 的 SYSTEM_PROMPT、messages() 前缀、ANSWER_SCHEMA 和固定生成参数，避免再次手工简化。

## 导入（不需要更新 Python 环境）

1. 浏览器打开仓库 dify/knowledge-qa-eval-aligned.ifpkg，使用 Download raw file 下载原始文件。
2. 在 Dify Studio 选择导入应用（Import App），选择该 .ifpkg。也可使用同目录的 .yaml；包格式与用户之前导出的格式一致。
3. 新应用名称为 knowledge-qa-eval-aligned。保留原应用，不覆盖之前测试记录。
4. 绑定现有 Ollama 模型凭据。继续使用之前已成功连接的本机地址；如果保留诊断转发器，可继续使用 http://host.docker.internal:11435，并保持转发终端运行。正常直连地址是 11434。
5. 确保检索服务监听 0.0.0.0:8765，Docker、Ollama 和已有 SSRF 放行配置仍生效。
6. Test Run 的 query 填“忘记统一账号密码，如何重置？”，运行一次。

如果 Dify 提示模型不可用，请在 LLM 节点选择你已经注册的 qwen3:4b 并复查参数；不要为导入删除旧模型。文件沿用用户导出包中的实际模型标识（含前导 Tab），从而尽量复用现有注册凭据，不把模型名清理混入本轮对比。前导 Tab 与干净名字的最小测试已显示相同生成量，未证明其造成速度差异。

## 新链路

User Input → 构造检索请求（Code）→ HTTP Request → 组装提示（Code）→ LLM → 解析模型 JSON（Code）→ Output。

Code 节点只做 JSON 组装/解析，不调用模型、网络或额外知识库，使用 Dify 自带代码沙箱。先构造请求字符串可正确处理问题中的引号和换行，不要求在 HTTP JSON 文本中手工插入变量。

- HTTP 接收动态 query，固定 top_k=3，不再写死密码重置问题。
- SYSTEM 与 qa_eval.py 完全一致。
- USER 与 qa_eval.messages(query, fragments) 一致，仅包含当前问题和已返回的 id/title/content；去掉 score、matched_keywords、接口包装字段。
- LLM 参数只有 temperature=0、seed=42、num_predict=2048、think=false，加上 json_schema。
- json_schema 使用官方 Ollama 插件参数，插件将其解析后作为请求顶层 format 对象发送。不仅依赖提示词中“请返回 JSON”的文字要求。
- 不设置 think_level、num_gpu、num_thread 或其他采样覆盖值。
- 输出 answer、insufficient、raw_answer、retrieval_body。无效 JSON 或字段缺失会报错，不偷偷改写成正确拒答。

正常答案形态类似：

```json
{"answer":"打开自助门户，选择密码重置，用绑定手机验证码验证后设置新密码。[IT-001]","insufficient":false}
```

工作流输出 answer 是解析后的中文，raw_answer 保留模型的 JSON 文本，retrieval_body 保留检索结果，供后续评测使用。

## 仍保留的差异和验证边界

Dify 的模型节点通常采用流式调用，本地脚本为 stream=false。模型上下文长度也可能受 Dify 模型注册配置控制，本地未显式传 num_ctx；诊断时检查实际 options，暂不修改运行资源参数。Dify 的推理标签分离仍沿用原设置。新增 Code 节点也会产生少量编排耗时。

因此本次统一的是问题、资料、提示词、输出 Schema 和显式生成参数，不宣称传输方式和全部隐式运行设置完全一致。比较时继续分别看 eval_count、eval_duration、load_duration 和节点总耗时，不把 Code/HTTP 等节点时间当成模型时间。

已在云端完成：包的清单哈希校验、代码节点逻辑测试、20 道既有题目的 USER 提示与本地逐字比较、SYSTEM/Schema/生成参数一致性检查、动态问题含引号和换行的检查。未连接用户 Mac 的 Dify，导入和真实模型生成仍需在 Mac 单题验证。如果导入或 Code 节点报错，保留错误和节点名称继续定位，不覆盖原应用。

诊断转发器预期看到正式 /api/chat 请求中 think_present=true、think=false、format_kind="schema"，options 内有 temperature=0、seed=42、num_predict=2048；stream 可能仍为 true。请区分凭据保存时 num_predict=5 的连通性校验和正式请求。

## 开发者重新生成

Mac 用户只导入包，不需要执行此步骤。修改本地提示词或 Schema 后，维护者执行：

```bash
python -m pip install -r requirements-dev.txt
python tools/build_dify_alignment.py
python -m unittest discover -s tests -v
```

生成器需 PyYAML；测试不执行来自用户导出包的任意代码，只执行仓库中生成器明确创建的 JSON 组装/解析代码。
