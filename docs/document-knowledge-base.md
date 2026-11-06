# 第四阶段：自有文档向量知识库

## 当前状态

用户已在Mac完成三份TXT上传、Embedding配置、向量检索测试，并在复制出的Dify应用中跑通单题全链路。最新截图显示所有节点成功。当前没有新语料的批量评测文件，也未将该新应用导出包上传仓库。

三份文档仍是虚构测试资料，不是实际企业文件。它们用于验证“资料入库 → 检索 → 回答”的通用流程，可在后续替换为有权使用的真实资料。

## 准备的资料

下载 [TXT材料包](../knowledge-base/it-demo-materials.zip)，解压后只上传以下文件：

- 01-account-and-mfa.txt：账号、密码、多因素认证、软件审批。
- 02-network-and-vpn.txt：无线网络、VPN、投屏。
- 03-shared-files-and-recovery.txt：共享权限、回收站、备份和历史版本。

同目录Markdown为源文件，TXT正文与其按字节一致。用户实测同内容TXT上传成功、MD失败，具体原因未确定，不能据此认为Dify普遍不支持Markdown。不要把同内容的TXT和MD同时导入；[材料说明](../knowledge-base/README.md)含参考题，不上传知识库。

## 本机组件和模型

- Mac总内存16GB，Docker目前约6GB；先小批量，不同时运行大批入库和问答评测。
- Ollama qwen3:4b负责回答，bge-m3负责Embedding，两者用途不同。
- Dify模型供应商为Ollama，Base URL为http://host.docker.internal:11434，不经过诊断器11435。
- bge-m3类型为Text Embedding，模型上下文上限按8192 tokens配置；不是把分段长度设成8192。

首次需要下载模型时：

```bash
ollama pull bge-m3
```

没有必要因重新打开项目再次下载。入库期间如需释放内存，可用ollama stop qwen3:4b卸载已加载的回答模型，不会删除模型；问答时会重新加载。

## 已采用的入库起始配置

| 选项 | 起始值 |
|---|---|
| 分段 | General |
| 分隔符 | `\n` |
| 最大长度 | 600字符 |
| 重叠 | 50字符 |
| 连续空格/换行/制表符处理 | 勾选 |
| 删除URL与邮箱 | 不勾选 |
| 自动摘要、Q&A分段 | 关闭 |
| Index Method | High Quality |
| Embedding | Ollama bge-m3 |
| 检索 | Vector Search |
| Top K | 3 |
| 阈值和Rerank | 起始时关闭 |

最大长度是上限，不代表每片一定有600字符。现有检索测试显示部分片段不足100字符；当前能找到所需信息，后续再检查条件、例外与数字是否被分开，不为完整流程验收先调优。

处理完成后，在Retrieval Test中测试“文件删除超过30天，但还没到90天，是否保证能恢复？”。用户已看到第三份文件的Chunk-17、18、16，包含超过30天不能保证自助恢复、90天备份不保证恢复及30个自然日。检索分数0.75–0.76为相关性分数，不是概率或回答正确率。

## 复制应用后的连线

保留knowledge-qa-eval-aligned，把副本作为文档知识库应用，例如knowledge-qa-docs。

```text
User Input(query)
→ Knowledge Retrieval(result)
→ 组装提示(query, result → user_prompt, retrieval_body)
→ LLM
→ 解析模型JSON
→ Output
```

在新应用中移除旧“构造检索请求”和“HTTP Request”节点，不保留通向旧检索的并行分支。

### Knowledge Retrieval

绑定User Input → query，选择包含三份文档的知识库（若文档分别建在多个库中，应选择需要使用的全部库），Vector Search、top_k=3。不要把文件恢复文档的单库检索误认为已经覆盖账号和网络资料。

### 组装提示Code节点

输入为query（User Input的query）和result（Knowledge Retrieval的result），不再读取旧HTTP的body/status_code。逻辑将结果的content、文档名称转换为fragments，并构造user_prompt与retrieval_body两个String输出。

每次返回片段按顺序分配KB-1、KB-2等别名用于本次回答引用。当前手动代码没有保留完整segment_id等元数据，别名不能作为长期评测的稳定ID。

节点名称可能仍叫“组装与本地一致的提示”，这是从旧应用沿用的名称；新输入结构已改变，不代表它与原20题应用的上下文逐字相同。

### LLM

- USER只引用当前组装节点的user_prompt变量，不能残留已删除节点的黄色/红色失效引用。
- SYSTEM要求依据当前资料回答、事实引用如[KB-1]、资料不足时明确说明，并输出answer/insufficient JSON。
- 生成参数沿用temperature=0、seed=42、num_predict=2048、think=false和原JSON Schema，其他资源/采样覆盖不启用。
- 推理标签分离可保持开启，但不把它当作关闭思考的证据。

### JSON解析与Output

JSON解析校验answer为非空字符串、insufficient为布尔值，不评价语义正确性。

| Output名称 | 来源 |
|---|---|
| answer | 解析模型JSON → answer |
| insufficient | 解析模型JSON → insufficient |
| raw_answer | 解析模型JSON → raw_answer |
| retrieval_body | 组装提示 → retrieval_body |

失效的旧HTTP body引用应删除后重新通过选择器绑定，不能手打变量名代替变量。

## 日常演示

打开Docker Desktop和Ollama，确认Dify服务已启动，进入新文档知识库应用Test Run。此分支不依赖原FastAPI 8765服务。

先用上述恢复题测试，并查看检索资料和最终回答。预期根据返回资料说明“不保证恢复”，引用本次KB编号。再换成“密码重置的验证码多久有效？”验证能找回账号文档，以确认不是只选中了第三份文件。

本次全节点成功截图保存在对话中，仓库仅记录可见数字及范围说明，见 [单题验证记录](../reports/knowledge-base-smoke/README.md)。没有完整输出时不单凭绿色勾选判断答案正确。

## 下一步复现与评测

1. 在Dify导出新应用（不含凭据）归档，知识库ID和凭据仍需在其他机器重新绑定；导出工作流不等于导出文档和向量索引。
2. 为三份文档准备新的有答案与无答案题，标注文档、章节及实际片段证据。材料README的9题可作为人工探索，不是已跑出的正式评测结果。
3. 保留原始document_id、segment_id、文档名、版本和正文，再接新应用API与独立评测器。

不要直接运行现有dify_eval.py评新库：该脚本检查旧20题、IT-ID引用和12条JSON资料，遇到KB-ID与新正文会报不一致。需要先适配再批量运行。
