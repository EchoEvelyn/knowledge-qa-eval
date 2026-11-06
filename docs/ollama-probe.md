# 检查 Dify 实际发给 Ollama 的参数

用于定位“DSL 已保存 think=false，但 Dify 仍有 Thinking 或生成较慢”。此服务只观察并转发，不纠正、补齐、删除参数；没有把诊断变成新的问答策略。无需重跑 20 题。

## 启动

将仓库的 ollama_probe.py 下载到原项目目录即可。该文件仅使用 Python 标准库，不需要更新环境或安装依赖。Mac 上保持原 Ollama 服务监听 11434，另开终端：

```bash
cd /Users/echo/Desktop/knowledge-qa-eval-main
python3 ollama_probe.py
```

服务默认监听 0.0.0.0:11435，以便 Docker Desktop 中的 Dify 访问；上游固定为本机 127.0.0.1:11434。监听地址可用 --host 修改。它是临时本地诊断工具，没有自身认证，不应暴露到公网，用完立即停止。

终端会打印日志路径 work/ollama-probe-日期时间.jsonl。若 11435 已被占用，指定 --port 11436，并相应调整后面的 Dify 地址。不能把代理监听端口和上游端口设成同一个。

## Dify 临时连接

进入 Integrations → Model Provider → Ollama → Manage Credentials，编辑当前使用的凭据，将 Base URL 从：

```text
http://host.docker.internal:11434
```

临时改为：

```text
http://host.docker.internal:11435
```

不要修改 HTTP 检索节点的 8765 地址，不改变模型、提示词和参数。保存凭据时可能触发模型验证请求，这也会出现在诊断日志中；回工作流运行一次同样的问题，关注该次 /api/chat 请求和对应 request_id 的响应。

## 看什么

请求事件只记录白名单参数：

- model、model_has_whitespace：模型名及前后是否存在空白，不会自动修正它。
- think_present：请求顶层是否存在 think 字段。
- think：实际值；只有 think_present=true 且 think=false 才证明显式传了关闭。
- options：temperature、seed、num_predict、num_gpu、num_thread、num_ctx 等相关数值；查看 think 是否被错放在 options 中。
- stream 和 format_kind：记录是否流式、是否 Schema，不记录 Schema 内容。

响应事件记录 elapsed_seconds、first_byte_seconds、status、thinking_chars、content_chars、content_has_think_tag，以及 Ollama 最后消息中的计时和 token 数。Ollama 的 *_duration 单位为纳秒，除以 1,000,000,000 才是秒；代理的 *_seconds 已是秒。

thinking_chars 统计 Ollama thinking 字段的字符数，content_has_think_tag 检查正文中是否出现 think 标签，均不保留文本。首字节可能属于思考，并非正式答案。未出现 thinking 字段或标签也不能证明绝对没有推理，只能说明响应中没有观察到这两种标记。

- 没有顶层 think=false：定位 Dify/插件在参数传递中的问题。
- 已传 think=false，但返回 thinking：继续检查本机 Ollama 及实际模型，不再反复调整 Dify 面板。
- 没有 thinking 但仍慢：对照 load_duration、eval_duration、eval_count，检查加载、输出长度及计算速度。

HTTP 状态、请求正文和响应正文透传；NDJSON 边生成边转发，不等完整回答结束。仅转发聊天、生成和必要的模型查看端点，不提供模型删除/下载功能。去除逐跳请求头并重新构建传输封装，不承诺字节级 HTTP 报文完全相同。原始 JSON 请求体与上游响应正文保持不变。

## 日志内容与结束

日志不保存问题、资料、完整响应、Authorization 或 API Key；仅记录上述允许的诊断字段。生成文本只在转发时经过进程内存，日志文件不会落盘这些文本。配置及计时用于本次定位，不作为新的准确率报告或可靠性能基准。

测试后把日志中对应的 request 和 response 两条记录提供给助手即可。先把 Dify Base URL 恢复成 http://host.docker.internal:11434，再在诊断终端按 Control+C 停止。Ollama 和检索服务继续保持运行。

## 验证范围

云端工程测试使用明确的模拟 Ollama 上游，验证流式首段在生成完成前到达、请求体和响应体不变、错误码透传、缺失 think 与 false 的区分、模型空白识别和敏感正文不写日志。没有把测试桩计时当作 Mac 模型耗时。真实 Dify → 转发器 → 本机 Ollama 的参数与计时需要在 Mac 上完成上述单题测试。
