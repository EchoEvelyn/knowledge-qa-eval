# 第三阶段：已发布 Dify 工作流的批量评测

使用原始 20 题和评分标准，脚本只调用 Dify 的 POST /v1/workflows/run，由 Dify 完成检索和 Ollama 生成。不会由脚本额外调用一次本地检索或模型，不需要恢复诊断转发器。

## Mac 前置条件

- Docker 中 qa-dify 服务正常，aligned 工作流已发布当前版本。
- Mac 的一个 FastAPI 检索服务监听 0.0.0.0:8765，原有 SSRF 单IP放行仍生效。
- Ollama 应用运行，Dify 凭据直连 http://host.docker.internal:11434。
- 已在这个 aligned 应用的 Access Point → Backend Service API → API Key 创建密钥。不能使用另一个应用的密钥。
- 已验证 http://localhost/v1/parameters 不带鉴权返回 401，这证明路由可达，不代表密钥或工作流已通过验证。

API 页面显示的 http://127.0.0.1:5001/v1 是容器内服务端口；原 Compose 没有把 5001 映射到 Mac，本例通过 Nginx 的 80 端口使用 http://localhost/v1。

## 更新方式

ZIP 用户只需从 GitHub 下载新增 dify_eval.py，放在原 knowledge-qa-eval-main 目录，与 qa_eval.py、manage.py 同级。不修改现有环境，不删除 reports；脚本复用现有两个模块，只用 Python 标准库。完整代码更新仍可下载新 ZIP，但本步骤不需要重建环境或重新部署 Dify。

## 先试一题

在 Mac 普通终端执行：

```bash
cd /Users/echo/Desktop/knowledge-qa-eval-main
python3 dify_eval.py run --smoke
```

脚本提示“请输入 Dify API Key”时，粘贴该应用的密钥并回车。输入不会显示，不写入日志或结果文件；不要把密钥贴到聊天、代码、命令行参数或 GitHub。也可从已配置的 DIFY_API_KEY 环境变量读取。review/report 不需要密钥。

先以带鉴权的 GET /parameters 检查 query 输入，再 POST /workflows/run：

```json
{
  "inputs": {"query": "忘记统一账号密码，如何重置？"},
  "response_mode": "blocking",
  "user": "knowledge-qa-eval"
}
```

只发送当前问题，不发送正确 ID 或参考答案。HTTP 200 且 data.status=succeeded、输出及检索内容通过校验后才算程序成功。预期 Q01 ok、retrieval_hit=True。结果默认放入 reports/phase3/时间戳/，smoke 明确只处理 1/20。

也可以仅做鉴权和输入预检，不调用模型：

```bash
python3 dify_eval.py check
```

## 正式跑原 20 题

```bash
python3 dify_eval.py run --output-dir reports/dify-run-01
```

如未设置环境变量，会再次提示输入密钥。输出目录必须不存在，避免覆盖。省略 --output-dir 则使用新的时间戳目录。可用 --timeout 300 延长每次 API 等待时间；默认 180 秒。按原题顺序串行运行，不并发，也不自动重试可能已在服务端执行的工作流。超时后 Dify 可能仍在执行，请在 Dify Logs 查看，客户端超时不等于服务端取消。

生成结束后沿用同样人工标准评分、查看报告：

```bash
python3 dify_eval.py review --run-dir reports/dify-run-01
python3 dify_eval.py report --run-dir reports/dify-run-01 --open
```

y 正确、n 错误、s 待评、q 退出，退出后可重复 review 继续。评分不需要再次调用 Dify。失败题保持独立错误，不当作正确拒答，也不从固定分母中删除。原 Mac 基线归档不变。

## 保存内容和耗时口径

- results.json：每题问题、Dify 请求体（不含 Authorization）、工作流响应、workflow_run_id、workflow_id、工作流状态、完整检索片段、回答、引用检查、检索命中、人工评分及理由。
- results.csv：原逐题字段加 workflow_run_id、workflow_elapsed_seconds、workflow_total_tokens。
- summary.md、report.html：第三阶段汇总及离线逐题报告。summary 在评分后仍标记为第三阶段。
- 运行级记录：本地数据与代码 SHA256、Python/平台、API 地址、/parameters 及可用时的 /info。响应里出现的密钥字段或确切密钥值会遮盖，不保存请求头。

对每道题：

- api_seconds 是 Mac 发出请求到读取完整 Dify 响应的时间。
- total_seconds 是客户端请求加本地结果校验，不含写文件和人工评分。
- workflow_elapsed_seconds 来自 Dify data.elapsed_time，是工作流级服务端时间，不是 Ollama 模型生成时间。
- workflow_total_tokens 来自 Dify，可能采用不同计数方式，不能直接当作 Ollama eval_count。
- blocking 响应没有提供节点分段耗时，所以 retrieval_seconds、generation_seconds 留空。节点耗时继续在 Dify TRACING 查看；不从两个总耗时相减推算“Dify 编排开销”。

不再把预期 model/top_k 字段说成 API 观察到的内部模型配置；题集结果中的 top_k 和返回问题会实查，内部模型/Schema 参数以发布工作流和已完成的诊断为依据。

## 错误处理

- 401/403：密钥无效、应用不匹配或权限问题；预检失败不跑题，中途权限失效停止继续请求。
- 找不到 query：选择错误应用或未发布最新版本。
- data.status=failed：即使 HTTP 200 也记为工作流错误，保存 run ID 和具体错误，继续后续题。
- 缺少 raw_answer/retrieval_body：发布的不是 aligned 输出版本。
- 返回的 query 与本题不同、top_k 不是3：动态变量或工作流版本错误，不虚报检索命中。
- 片段 ID、标题或正文与本地固定语料不一致：记录数据不一致，停止对此题评分。
- JSON 格式、输出字段类型或解析结果与 raw_answer 不一致：记录错误，不静默修正。

每题完成后立即保存。Ctrl+C 保留已有结果；生成暂不支持断点续跑，重新运行应创建新目录。HTTP 重定向不携带密钥跟随，请使用最终 API 地址。

## 验证边界

云端已使用明确的 Dify HTTP 协议测试桩验证20题请求、保存与评分、401脱敏、工作流失败、错误query、输出缺失、超时、不重试和重定向拒绝；没有访问用户 Mac 的 Dify，也没有生成真实第三阶段正确率。实际鉴权、已发布工作流的一题试跑及20题评测需在 Mac 完成。

维护者运行检查：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
