# 知识库测评：指标、证据和复现

## 当前已经完成什么

原始记录保留在 `reports/kb-run-01/`。补充审核在 `evaluation/reviews/kb-run-01/`，计算报告在 `reports/kb-run-01-metric-audit/`。原始运行、用户评分均未改写。

| 指标 | 本轮结果 | 通俗解释 |
|---|---:|---|
| 请求成功率 | 20/20 | API是否成功返回，不代表回答正确 |
| 来源命中 | 15/15 | 找到了预期文档，不代表找到了答案段落 |
| 证据组完整命中 | 14/15 | 预设的每组答案证据是否全部覆盖 |
| Hit@3 | 14/15 | 前3段中至少有一段支持核心答案 |
| Precision@3 | 40% | 前3段中有多少直接支持核心答案 |
| MRR@3 | 0.90 | 第一条有效证据排得是否靠前 |
| Recall@3 | 待全量金标 | 全库所有相关片段中，找回多少 |
| 回答正确率 | 13/15 | 用户原评分；以完整答案为单位 |
| 正确拒答率 | 3/5 | 缺乏资料时是否明确说明不足 |
| 事实忠实度 | 36/37 | 拆分出的事实是否有返回证据支持 |
| 有支持且有引用的事实比例 | 34/37 | 事实正确且附上实际支持它的引用 |
| 引用组完全支持率 | 14/15 | 每个含引用回答的整组引用是否支持全部事实 |
| 平均/P95耗时 | 6.97秒 / 10.33秒 | 客户端20次请求，P95用nearest-rank |

检索指标在15道有答案题上计算，Precision固定除以3，相关等级2才计相关；等级1只是背景。MRR是各题首条相关片段排名倒数的平均值。没有相关片段时记0。

**这里的14/15是Hit或证据命中，不是严格Recall。** 60条返回记录去重后43段，无法证明包含全库所有相关段。严格Recall的分母必须来自完整片段快照和全部“题目×片段”的相关性标注。

补充语义审核由助手事后完成，不是独立盲评。表格包含原文、判断及理由，可复核。忠实度按事实计数，不等于回答正确率；纯拒答、复述问题、占位文字不作为事实忠实度满分，另用拒答/回答正确率衡量。引用组指标不是逐条引用的精确率。D09错在把个人电脑说成可接入，D11缺少答案证据，D16和D18未正确拒答。

## 在Mac查看现有报告

下载 `knowledge-base/kb-metrics-kit.zip`，解压到临时文件夹，将内容合并复制进原项目目录，不要用整个新文件夹替换旧项目。无需安装新依赖。

```bash
cd /Users/echo/Desktop/knowledge-qa-eval-main
python3 kb_metrics.py view --audit-dir reports/kb-run-01-metric-audit --open
```

HTML可直接在浏览器打开；report.md可直接复制到Obsidian。CSV逐题列出指标，JSON便于后续图表使用。

## 补齐严格Recall：只导出一次，不用重新生成20题

1. 保持Dify运行，导出期间暂停修改知识库、分段和索引。
2. 在Dify知识库的API访问入口创建**知识库API Key**。它和应用运行Key不是一类；不要把Key发到聊天或写进Git。
3. 执行：

```bash
cd /Users/echo/Desktop/knowledge-qa-eval-main
python3 kb_metrics.py export --api-base http://localhost/v1 --output-dir work/kb-corpus-export-01
```

按提示输入Key，选择实际被工作流使用且包含三份TXT的知识库序号；多个以逗号分隔。工具仅GET读取文档和全部已完成、启用的分段，自动翻页，保留知识库/文档/片段永久ID，不保存Key。若地址或API权限错误，工具会报错，不把失败导出当完整快照。

完成后提供 `work/kb-corpus-export-01/segments.json`，即可继续核对全部片段并完成金标。该文件包含知识库正文；本项目资料为虚构内容。

生成待审核表：

```bash
python3 kb_metrics.py prepare --corpus work/kb-corpus-export-01/segments.json --output-dir work/kb-qrels-01
```

逐对审核 `qrels.csv`：0无关，1仅背景，2支持至少一个核心事实；填写relevance、reviewer、reason。`suggested_only`只是短语匹配提示，不是已完成金标。必须覆盖15题×全部片段，不能只标返回Top3。

审核完成后计算：

```bash
python3 kb_metrics.py analyze --run-dir reports/kb-run-01 --reviews evaluation/reviews/kb-run-01 --corpus work/kb-corpus-export-01/segments.json --qrels-dir work/kb-qrels-01 --output-dir reports/kb-run-01-full-metrics
```

每题Recall=前3段找回的相关片段数/全库相关片段数，最后对15题宏平均。脚本校验快照和题集哈希、全部标注覆盖、审核字段及返回正文到永久ID的唯一对应关系；缺失、歧义或重新分段后无法对应时拒绝计算。原始结果不被覆盖，输出目录必须是新的。

## 复算已有补充审核

```bash
python3 kb_metrics.py analyze --run-dir reports/kb-run-01 --reviews evaluation/reviews/kb-run-01 --output-dir work/kb-metrics-recomputed-01
```

更改补充审核需要同步review manifest内CSV的SHA256以记录新版本；这用于检测意外修改，不代表审核客观正确。新一轮测试应建立独立审核目录，不能套用本轮片段金标。演示时并排展示检索指标、回答/拒答正确率和耗时，避免单看API成功率或忠实度。
