# 完整片段快照下的 Recall@3

基于用户2026-11-06导出的3份文档77个片段，助手对15道有答案题×77段共1155对进行事后语义审核。此为可复核的助手标注，不是人类独立盲评。原20题生成结果及用户正确性评分保持原样，没有重新调用模型。

- 全量原始快照：[segments.json](../../evaluation/corpora/kb-run-01/segments.json)
- 全量标注：[qrels.csv](../../evaluation/qrels/kb-run-01/qrels.csv)，含永久ID、正文、等级、审核者与理由
- 审核范围、哈希、局限：[review-manifest.json](../../evaluation/qrels/kb-run-01/review-manifest.json)
- [指标报告](report.md)、[逐题CSV](per-question.csv)、[机器可读JSON](metrics.json)、[离线HTML](report.html)

相关阈值为2：直接支持至少一个本题核心事实；1是背景或标题；0无关。不是要求每段覆盖整题。全库共有22个“题目—相关片段”对，Top3找回18个。**Recall@3采用逐题宏平均86.67%，不是18/22的微平均81.82%。** Precision@3=18/(15×3)=40%。无答案5题不进入这些检索指标分母。

| 题目 | Top3找回相关数 | 全库相关数 | Recall@3 |
|---|---:|---:|---:|
| D01 | 1 | 1 | 100% |
| D02 | 1 | 1 | 100% |
| D03 | 1 | 1 | 100% |
| D04 | 1 | 1 | 100% |
| D05 | 1 | 1 | 100% |
| D06 | 2 | 2 | 100% |
| D07 | 1 | 1 | 100% |
| D08 | 1 | 1 | 100% |
| D09 | 2 | 2 | 100% |
| D10 | 2 | 4 | 50% |
| D11 | 0 | 1 | 0% |
| D12 | 1 | 2 | 50% |
| D13 | 1 | 1 | 100% |
| D14 | 2 | 2 | 100% |
| D15 | 1 | 1 | 100% |

D10、D12已有足够回答题目的证据，但漏掉重复支持某一子问题的段落，因此Recall低于100%仍可能回答正确。D11真正遗漏报障所需错误码、发生时间、客户端版本。D09检索成功但回答把“仅受管电脑”说成“个人电脑可以”，是生成错误。这些指标不能互相替代。

原60条返回片段（43个去重正文）均与77段快照唯一匹配。快照晚于原测试，不能凭此证明历史测试时全库与导出时完全一致；本报告严格以该导出快照为相关性全集。若有重建或修改，应导出同版本并重新评测。短行标题、版本和日期被分别切段，解释了77段中有大量不含答案的片段；暂未更改分段配置。

复现命令（在仓库根目录）：

```bash
python3 kb_metrics.py analyze --run-dir reports/kb-run-01 --reviews evaluation/reviews/kb-run-01 --corpus evaluation/corpora/kb-run-01/segments.json --qrels-dir evaluation/qrels/kb-run-01 --output-dir work/kb-full-metrics-reproduced-01
```

早期`kb-run-01-metric-audit`继续保留，记录当时尚无全量片段的状态；本目录是新增的全量指标报告。
