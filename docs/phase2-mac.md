# 第二阶段：Mac 本机检索与 Ollama 问答评测

沿用 `app.py`、原始 `data/corpus.json` 和 `data/questions.json`，固定 `top_k=3`、`qwen3:4b`。不接 Dify。`qa_eval.py` 的 HTTP 客户端仅依赖 Python 标准库，FastAPI 服务依赖仍用原来的 `requirements.txt`。

## Mac 运行

安装 Python 3.10+（建议 3.12）和最新版 Ollama 的 macOS 应用。首次下载模型需要联网；模型下载后本流程均可在本机运行。以下终端均进入同一仓库根目录。

终端 1，启动 Ollama（已通过菜单栏应用运行则无需重复启动）：

```bash
ollama serve
```

终端 2，更新代码、下载模型并启动检索服务：

```bash
git clone https://github.com/EchoEvelyn/knowledge-qa-eval.git
cd knowledge-qa-eval
# 如果已有克隆，改为在该仓库运行 git pull --ff-only
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
ollama pull qwen3:4b
ollama list
curl --noproxy '*' http://127.0.0.1:11434/api/version
python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

终端 3，执行 20 题：

```bash
cd knowledge-qa-eval
source .venv/bin/activate
python qa_eval.py run \
  --retrieval-url http://127.0.0.1:8765 \
  --ollama-url http://127.0.0.1:11434 \
  --output-dir reports/mac-run-01
```

输出目录必须不存在，防止覆盖旧结果。省略 `--output-dir` 会自动创建带时间戳的新目录。初次加载模型可能较慢，可加 `--ollama-timeout 600`；检索超时默认 10 秒、Ollama 请求超时默认 180 秒。脚本顺序执行，不并发挤占本机资源。

默认只访问本机服务。Mac 和云端的 `127.0.0.1` 各自指向本机，云端这个地址不会访问到你的 Mac；无需为此把 Ollama 暴露到公网。

## 回答约束

对每题先 HTTP 调用 `/retrieval`，再将返回的至多 3 个片段的 ID、标题、正文交给 `/api/chat`。即使检索为空也调用模型，实际检查它是否说明资料不足。不会把全量语料、正确 ID 标签、参考答案、评分标准传给模型。

系统提示要求回答具体问题、事实后引用 `[IT-001]` 这样的片段 ID；资料不足时明确说“资料不足，无法回答”并说明缺什么。模型返回 JSON `answer` 和 `insufficient`。后者仅是模型自述，不是评分。使用 `stream=false`、`think=false`、`format=json`、`temperature=0`、`seed=42`、`num_predict=2048`；这有助于复现，但不同 Ollama 版本和硬件仍可能产生不同回答。

如果旧版 Ollama 不支持 `think=false`，请升级 Ollama，不要用云端模型替代本机 `qwen3:4b`。模型未安装、HTTP 失败、超时、空回答、格式错误、回答截断都会记录为错误，不会当作正确拒答。

## 三种指标分别记录

1. `retrieval_hit`：自动判断。15 道有答案题的正确 ID 是否出现在返回片段中；无答案题填 null。另统计无答案题返回非空的题号。
2. `answer_correct`：对 15 道有答案题人工评分。要覆盖 `data/answer_rubrics.json` 中的关键要点、无事实错误或捏造，并引用正确且实际检索到的片段。初始为 null，人工确认后才为 true/false。引用是否属于本次返回片段可自动检查，但不能自动证明内容正确。
3. `refusal_correct`：只对 5 道无答案题人工评分。明确说明资料不足，并且不猜测所问的具体答案，才为 true；把邮箱总容量 5GB 当作单封发送限制等回答判 false。不能只凭一句“资料不足”或模型 `insufficient=true` 判对。

有答案题 Q15 虽然应在检索为空时合理拒答，但仍未回答知识库本可回答的问题，所以 `answer_correct=false`，`refusal_correct=null`。分别记录可以区分检索漏检和回答幻觉。

评分命令：

```bash
python qa_eval.py review --run-dir reports/mac-run-01
```

终端逐题展示片段、回答、参考标准，输入 `y` 正确、`n` 错误、`s` 暂不评、`q` 退出，并填写理由。已评分题自动跳过，可退出后重复运行继续。每次评分立即更新结果文件。不要用模型自评代替这一步。

最终分别报告：检索命中数/15、回答正确数/15、正确拒答数/5。待评分不记为失败或成功；只有完成全部 20 题且相应字段全部评分后，才能报告完整正确率。程序/网络错误另列，不能把失败题从分母悄悄删除。

## 保存内容

每次运行目录包含：

- `results.json`：每题原始检索响应、完整片段、实际提示词、Ollama 原始响应、回答、引用 ID、检索命中、人工判定与理由、错误；以及模型 digest、Ollama 版本、运行平台、题集/语料/评分标准哈希。
- `results.csv`：逐题回答、返回 ID、指标与耗时；完整片段在 JSON。
- `summary.md`：命中数、错误题号、人工评分进度和正确数。

耗时使用单调时钟 `perf_counter`，单位秒，分别记录 `retrieval_seconds`、`generation_seconds`、`total_seconds`。生成耗时包含本次 Ollama HTTP 请求的模型加载及推理；首题可能包含冷启动。每题总耗时不包括将结果写入磁盘的时间。预检耗时不计入每题耗时。Ollama 自带纳秒计时及 token 统计原样保存在 `ollama_response`，不与上述秒单位混用。

每题完成（含失败）后原子更新 JSON；中途失败继续后续题，任何题失败返回非零退出码，已完成结果保留。Ctrl+C 后保留部分结果；重新运行会创建新评测，不把两次生成混成一轮。人工评分可继续，模型生成暂不支持断点续跑。

## 云端检查与 Mac 待检查

云端已完成：

- 原检索接口与 20 道题保持不变；新增参考评分标准不进入模型提示。
- 实际启动 FastAPI，通过真实 TCP HTTP 跑完 20 题，检索命中仍为 14/15；无答案题 Q16–Q18 有片段返回。
- 用明确标记为测试桩的 Ollama HTTP 服务验证 20 次串行调用、提示词与返回片段对应、输出文件、分段计时、人工评分与继续、HTTP 500 后继续及错误记录、预检失败、引用越界、非法/截断响应。
- 云端 `127.0.0.1:11434/api/version` 返回 connection refused。没有可用的 Mac 地址或隧道，本会话无法访问你的 Mac Ollama。

Mac 仍需完成：下载并加载真实 `qwen3:4b`，运行 20 道题，验证实际模型输出与引用，记录本机耗时，人工评分并得到回答正确数和正确拒答数。**没有把测试桩文本或计时作为真实 qwen3:4b 的评测结果。**

可在 Mac 复跑工程验证（启动测试用临时端口，自动清理，不需要 Ollama）：

```bash
python -m unittest discover -s tests -v
```
