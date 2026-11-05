# 第二阶段统一运行与交付

目标是完整打通“能运行、能保存、能评分、能复现”。本次保留已有检索规则、题集、提示词和模型参数，不优化准确率，不接 Dify。

## 已交付证据

[Mac 实测归档](../reports/mac-run-02/README.md) 包含用户上传的 20 题原始结果、人工评分、耗时、文件哈希，以及离线 HTML 阅读版。原始文件不经过重新导出；新增入口不会改变它们。`run_state=completed` 表示运行完成，不表示答案全对。

## Mac 首次准备

使用 Python 3.10+（本次 Mac 实测为 3.11.9）。打开 Ollama 应用，下载模型：

```bash
ollama pull qwen3:4b
```

在项目文件夹打开终端。如果仍使用桌面 ZIP 目录：

```bash
cd /Users/echo/Desktop/knowledge-qa-eval-main
python3 -m venv qa
source qa/bin/activate
python -m pip install -r requirements.txt
```

已有 `qa` 环境时只需激活，无需重新创建。`qa/` 已加入 .gitignore。

如果项目来自 ZIP，不能在其中使用 git pull 更新：从 GitHub 下载最新 ZIP，解压到另一个新目录，保留原目录的 qa 和 reports。在新目录创建 qa 环境后执行后面的命令即可；不要用新 ZIP 覆盖旧结果。新版本至少包含 manage.py、qa_eval.py、app.py、requirements.txt、data/。使用 git 克隆的目录可用 git pull --ff-only。

## 一次命令运行

保持 Ollama 应用运行；在已激活 qa 的项目终端执行：

```bash
python manage.py run
```

它会：检查 Ollama 可连接且安装 qwen3:4b → 检查检索端口 → 自动启动缺少的检索服务，或复用现有可用服务 → 固定 top_k=3 跑原 20 题 → 保存结果与 HTML 报告 → 关闭本次自动启动的检索服务。不会关闭用户原来启动的检索服务或 Ollama。

默认在 reports/phase2/ 下创建时间戳目录，不覆盖归档。屏幕会打印实际目录。想在生成完成后紧接着评分：

```bash
python manage.py run --review
```

如果只想先验证连通性和一题生成：

```bash
python manage.py run --smoke
```

单题结果明确标记为 smoke，不能当作 20 题评测。可用 `--output-dir reports/local-run-03` 指定不存在的新目录；可用 `--ollama-timeout 600` 增加模型加载超时。端口默认 8765，可用 `--port 8766`。Ollama 默认地址 http://127.0.0.1:11434，可用 --ollama-url 调整。模型固定为 qwen3:4b。

## 一次命令查看报告

```bash
python manage.py report --open
```

默认读取 reports/phase2/ 中最近更新的结果，生成 report.html 并用默认浏览器打开。无需运行模型、检索服务或网络；不修改原始结果 JSON。浏览器未自动打开时，在打印的目录中双击 report.html。

查看已归档的真实 Mac 基线：

```bash
python manage.py report --run-dir reports/mac-run-02 --open
```

不加 --open 则只生成报告并在终端显示路径和统计，适用于无桌面的环境。

## 人工评分

```bash
python manage.py review
```

默认评分最近更新的本地结果；也可用 --run-dir 指定目录。按 y/n/s/q 评分，每次确认立即保存，重复运行可继续。结束后自动刷新 HTML。只有全部生成且全部评分后，才能报告完整正确率。

Mac 归档目录有 provenance.json 标识，统一入口拒绝修改其评分；复测使用新目录。评分结果分别记录 answer_correct 和 refusal_correct，检索命中自动记录。详细评分标准见 [原有 Mac 指南](phase2-mac.md)。

## 仅做预检

已有检索服务运行时，可检查现有服务：

```bash
python manage.py check
```

此命令只检查，不启动服务。检索未运行时请使用 run 自动启动，或按旧指南手动启动 Uvicorn。预检验证 Ollama 版本、模型已安装，检索对已知问题返回 IT-001、无匹配返回空列表；旧评测脚本的缺参 HTTP 验证继续由 evaluate.py 保留。

## 每轮保存什么

- results.json、results.csv、summary.md：片段、回答、分段耗时、接口错误、人工评分、模型 digest、Ollama/Python/平台版本、数据哈希。
- workflow.json：统一入口预检结果、服务是自动启动还是复用、代码和 requirements.txt 哈希；ZIP 安装无 Git 提交号也能追踪代码版本。
- report.html：可离线打开的逐题报告，资料在每行展开查看。
- work/retrieval-*.log：自动启动的检索服务日志。

程序返回 0 表示工程运行成功，不表示全答对或评分完成。题目生成失败返回非零退出码，已完成结果保留并生成报告。Ollama 不可用时在生成前提示打开应用；缺模型时提示 pull，不自动下载安装。端口被其他服务占用且接口不符时停止，不覆盖或终止该服务。

## 本次验证边界与验收

原始 20 题真实 qwen3:4b 生成和人工评分已由用户在 Mac 完成，作为归档证据。新增统一入口在云端以真实 FastAPI + 明确标记的 Ollama 协议测试桩验证：自动启动/清理、复用现有服务、错误保留、模型预检失败、报告生成和归档完整性。工程测试不作为新的真实模型评测。

Mac 仍需对新增入口做一次验收：

1. 激活 qa，打开 Ollama，执行 python manage.py run --smoke。
2. 确认一题完成，目录中出现 results.json、workflow.json、report.html。
3. 执行 python manage.py report --open，确认浏览器能打开报告。
4. 需要新的完整一轮时再执行 python manage.py run --review。

已有 mac-run-02 不必为整理交付而重跑。可在 Mac 或云端运行工程检查：

```bash
python -m unittest discover -s tests -v
```
