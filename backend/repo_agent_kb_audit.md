# comlan智标：Agent、模型与企业知识库只读审计

审计日期：2026-07-23  
审计范围：`D:\comlan智标`、`D:\comlan智标\backend`  
审计方式：代码与配置只读检查、符号及引用检索、已有数据聚合统计、Chroma SQLite 只读查询、无缓存单元测试。  
审计限制：未重新入库，未调用知识库写接口，未联网，未调用真实模型，未生成正文或 DOCX，未读取 `reference_only` 历史投标文件。

## 1. 执行摘要

仓库不是“没有 Agent 和知识库的空项目”，但现有能力处于不同代际：

1. 当前技术标规划链已经可用：
   `technical_response_outline.json → technical_writing_outline.json → technical_agent_context_loader` 能稳定生成章节级最小上下文。
2. 仓库已有统一模型 Provider 层：
   支持 OpenAI-compatible 协议、火山引擎 Ark、Responses/Chat Completions、结构化 JSON、超时、重试、错误映射、用量记录和一次 Schema 修复。
3. 仓库已有章节草稿生成代码：
   `chapter_drafting_agent.py` 和 `chapter_drafting_pilot.py` 具备结构化输出、事实边界、覆盖校验、占位符、Markdown 渲染等可复用能力。
4. 旧章节草稿链不能直接接收当前医院项目上下文：
   它硬性依赖六个旧中间文件、九个固定章节和旧 `task_id` 体系。
5. 企业知识库存在真实文件、解析文本、结构化人员信息和证据目录，但不是真实语义 RAG：
   Chroma 使用 SHA-256 伪 embedding，当前仅有 13 个分块且全部来自同一份资质文件。
6. 当前真正可用的知识检索主要是：
   `metadata.json` 上的结构化字段读取、关键词匹配、材料分组匹配和证据候选构建。
7. Agent 没有 function calling、工具注册表或工具调用循环：
   现有章节 Agent 接收预先编译的证据包，不会自主调用知识库；旧 `SectionService` 虽先查 Chroma，但正文由 mock 生成。
8. 仓库没有网页搜索、抓取或浏览器工具：
   当前只有联网需求规划，没有执行器。
9. 推荐方案是 **方案B：复用模型和工具层，新建轻量技术写作编排器**。
   不建议重做模型客户端、解析链、知识库文件管理，也不建议把旧九章工作流整体迁移到医院项目。

关键结论：

| 问题 | 结论 |
| --- | --- |
| 是否已有可用 Agent | 有可复用的章节生成内核和模型调用链；旧通用 AgentRunner 主要是 mock，不能直接作为正式写作 Agent |
| 是否已有可用本地知识库 | 有，文件、解析文本、人员结构化事实和材料候选查询可用，但数据量小且企业事实不完整 |
| 是否是真实 RAG | 否；当前主要是关键词/结构化查询，Chroma 使用 mock embedding |
| Agent 是否能调用知识库 | 没有自主工具调用；现有流程在 Agent 调用前离线准备证据 |
| 是否已有联网工具 | 没有 |
| 是否已有统一模型 Provider | 有，真实模型入口已统一到 OpenAI-compatible Provider 层 |
| `technical_agent_context_loader` 应接到哪里 | 接到轻量编排器，再复用 `LLMClient`、章节 Schema 和校验函数 |
| 是否需要新增 `TechnicalWritingAgent` | 不需要建设完整自治 Agent；需要一个轻量章节编排器 |

## 2. 项目相关目录和入口

### 2.1 精简目录树

```text
D:\comlan智标
├─ src/                              # Vite/React 前端
├─ scripts/                          # 根目录评估脚本
├─ backend/
│  ├─ cli.py                         # 后端唯一明确应用入口
│  ├─ config.py                      # 存储、metadata、Chroma 路径
│  ├─ requirements.txt
│  ├─ app/                           # 解析、知识库、规划、Agent、模型、文档代码
│  ├─ agents/                        # Agent system prompt、user template、JSON Schema
│  ├─ config/                        # LLM profile 配置
│  ├─ resources/writing/             # 公司通用写作大纲
│  ├─ storage/
│  │  ├─ original/                   # 原始入库文件
│  │  ├─ parsed/                     # 转换或解析产物
│  │  └─ assets/                     # 图片/OCR资产
│  ├─ data/
│  │  ├─ metadata.json               # 主要知识库 metadata 与结构化事实
│  │  ├─ chroma/chroma.sqlite3       # Chroma 持久库
│  │  ├─ tender_projects.json        # 项目图、旧规划状态
│  │  └─ manual_reviews/             # 人工证据决策
│  ├─ outputs/                       # 项目输出
│  ├─ tests/                         # pytest 测试
│  └─ scripts/                       # 辅助验证脚本
└─ package.json
```

### 2.2 入口判断

- CLI 入口：`backend/cli.py:133`，共注册文件入库、解析、知识库查询、规划、Agent、模型检查、技术大纲和上下文等命令。
- API 入口：未发现 FastAPI、Flask、APIRouter 或其他后端 HTTP API。
- 前端入口：`src/main.tsx`、`src/App.tsx`，但本次范围内未发现与 Python 后端的正式 API 服务链。
- 模型入口：`app/llm_client.py:get_llm_client`。
- 企业知识库入口：
  - 文件与 Chroma：`app/kb_service.py:KnowledgeBaseService`
  - 结构化查询：`app/enterprise_knowledge_query.py`
  - Metadata：`app/metadata_store.py`
- 当前技术写作入口：
  - `cli.py build-technical-agent-context`
  - `app/technical_agent_context_loader.py`
- 旧技术草稿入口：
  - `cli.py build-technical-draft`
  - `cli.py build-technical-draft-pilot`

## 3. 已有 Agent 清单

| 组件 | 路径 | 入口 | 是否被实际调用 | 工具能力 | 测试 | 可复用性 |
| --- | --- | --- | --- | --- | --- | --- |
| 通用 AgentRunner | `app/agent_runner.py` | `run_agent()` | CLI `run-agent`、旧草稿生成器调用 | 无工具；只加载 system prompt 和 Schema | 有 | 低；真实 Provider 接口不兼容旧 `generate_json` 路径 |
| business_writer | `agents/business_writer/` | AgentRunner 配置 | 旧 `agent_draft_generator` 调用 | 输入仅含预制 evidence refs | 有 mock 测试 | 低到中 |
| technical_writer 配置 | `agents/technical_writer/` | AgentRunner 配置 | 旧草稿生成器调用；Schema 被新章节链复用 | 无自主工具 | 有 | Schema 可高复用，旧 AgentRunner 调用低复用 |
| 章节草稿 Agent | `app/chapter_drafting_agent.py` | `build_technical_draft()`、`build_ai_chapter()` | CLI 实际引用 | 无工具调用；读取预编译证据和上下文 | 有大量测试 | 中到高；模型、Schema、校验可复用，输入链需适配 |
| 双阶段章节试点 | `app/chapter_drafting_pilot.py` | `build_technical_draft_pilot()` | CLI 实际引用；存在一次真实请求记录 | 无工具调用；深度撰写后结构归一化 | 有 | 中；当前实现绑定旧九章链和服务方案目录 |
| 写作上下文编译器 | `app/writing_context_compiler.py` | `build_writing_context()` | CLI 实际引用 | 预编译证据、占位符、guardrails | 有 | 中；理念可复用，输出结构不适配当前八章链 |
| 旧写作编排器 | `app/writing_orchestrator.py` | `build_writing_plan()` | CLI 实际引用 | 使用已匹配材料，不调用模型 | 有 | 低；属于旧 generation task 链 |
| Agent 草稿生成器 | `app/agent_draft_generator.py` | `generate_agent_drafts()` | CLI 实际引用 | 调 business/technical writer | 有 | 低；结果明确标记 `generated_mock` |
| SectionService | `app/section_service.py` | `generate_section()` | CLI `generate-section` | 先查 Chroma，再调用 mock | 无专门真实链测试 | 低；不应作为正式章节生成器 |
| ReviewService | `app/review_service.py` | `review_section()` | CLI `review-section` | 无工具，纯 mock | 无真实审查测试 | 低 |
| chapter_review/full_review/revision | `agents/*/` | Prompt 与 Schema | 未发现正式模型调用链 | 设计上为审查/修订，实际无工具 | 无专门运行测试 | 低；配置占位 |
| kb_search | `agents/kb_search/` | Prompt 与 Schema | 未发现调用 | 设计为检索 Agent，未绑定查询函数 | 无 | 低 |
| response_matrix | `agents/response_matrix/` | Prompt 与 Schema | 未调用；实际由确定性 builder 完成 | 无 | builder 有相关测试 | 配置低，builder 中 |
| document_splitter | `agents/document_splitter/` | Prompt 与 Schema | Prompt 未调用；实际由规则 `split_document()` 完成 | 文件解析规则 | 有脚本验证 | 配置低，规则实现中 |
| tender_parser_agent/tender_parse | `agents/*/` | Prompt 与 Schema | `kb_service` 只读取名称，结果来自 `mock_tender_parse` | 无 | 无真实模型解析测试 | 低 |
| score_table_parser_agent | `agents/score_table_parser_agent/` | Prompt 与 Schema | 配置被加载，但实际用 `score_table_locator` | 无 | locator 有相关测试 | 配置低，规则实现中 |
| tender_merge_agent | `agents/tender_merge_agent/` | Prompt 与 Schema | 配置被加载，实际由 `build_tender_project_graph` 合并 | 无 | 间接测试 | 配置低，确定性合并中 |
| 当前技术写作规划器组 | `response_outline_builder.py`、`technical_writing_outline_*.py`、`technical_section_task_planner.py`、`draft_unit_planner.py`、`technical_agent_context_loader.py` | 各 `build/load/plan` 函数 | CLI 实际引用 | 无模型、无工具执行 | 有 | 高；是当前正式规划链 |
| 证据覆盖与人工决策链 | `evidence_coverage_builder.py`、`evidence_resolution.py` | `build_evidence_coverage()`、`merge_decision_input()` | CLI 实际引用 | 结构化材料查询、证据状态与人工确认 | 有 | 中到高；可封装成只读 Agent 工具 |

结论：仓库中有“Agent 名称和提示词”的组件很多，但真正使用真实模型的核心只有章节草稿链；多数其他 Agent 是配置、mock 或确定性规则的命名包装。

## 4. Agent 真实调用链

### 4.1 当前正式技术规划链

```text
CLI build-response-outline
→ response_outline_builder
→ technical_response_outline.json

CLI build-technical-writing-outline
→ company outline loader/validator
→ tender format analyzer
→ writing outline adapter/refiner
→ technical_writing_outline.json

CLI plan-technical-writing-information
→ task information planner
→ writing outline enricher
→ writing_requirements 写回节点

CLI build-technical-agent-context
→ technical_agent_context_loader
→ 按 chapter_id/section_id 查询 response_point_id
→ 最小章节上下文
```

该链不调用模型、知识库或网络。

### 4.2 旧通用 AgentRunner 链

```text
CLI generate-agent-drafts/run-agent
→ agent_draft_generator / agent_runner
→ load agents/<name>/system.txt + output_schema.json
→ get_llm_client(provider="mock")
→ MockLLMClient.generate_json()
```

关键限制：

- `run_agent()` 默认强制使用 `provider="mock"`。
- 传入其他 provider 字符串会被 `get_llm_client()` 拒绝。
- 不传 provider 时得到的真实 `OpenAICompatibleLLMClient` 只实现 `generate_structured()`，没有旧接口要求的 `generate_json()`。
- `user_template.txt` 没有被 AgentRunner 加载或渲染。

因此这条链不能直接升级为正式技术写作 Agent。

### 4.3 旧真实章节模型链

```text
CLI build-technical-draft
→ chapter_drafting_agent.build_technical_draft
→ 读取六个旧写作上下文文件
→ build_chapter_prompts
→ get_llm_client(role="drafting")
→ OpenAICompatibleLLMClient.generate_structured
→ 火山 Ark / OpenAI-compatible SDK
→ 本地 JSON Schema 与事实边界校验
→ chapter_drafts.json + Markdown
```

双阶段试点：

```text
CLI build-technical-draft-pilot
→ drafting profile 深度撰写
→ fast_task profile 结构归一化
→ fidelity/coverage/schema 校验
```

真实运行证据：

- 已有一次火山模型请求记录，Provider 返回了响应并记录 request ID、token 和截断原因。
- 该次请求因 16,000 输出 token 达到上限、JSON 未闭合而失败。
- 当前已有的九章草稿产物是 `deterministic_preview`，不是 AI 正式草稿。

### 4.4 知识与 Agent 的连接方式

当前不是工具调用，而是离线编译：

```text
metadata.json
→ enterprise_knowledge_query / evidence_coverage_builder
→ evidence catalog / evidence use map
→ writing_context_compiler
→ chapter_task_packets
→ chapter_drafting_agent
```

Agent 本身不会主动决定或执行 `query()`。

## 5. 模型调用能力

### 5.1 Provider 支持

- 统一接口：`app/llm_client.py:LLMClient`
- 真实实现：`app/openai_compatible_llm_client.py:OpenAICompatibleLLMClient`
- 配置：`app/llm_profiles.py`
- 当前 Provider：`volcengine_ark`
- 协议：`openai_compatible`
- API 模式：
  - Responses API
  - Chat Completions
- 角色 Profiles：
  - drafting
  - fast_task
  - review

当前配置的三个角色均显示 API key 和模型已配置；审计未读取或输出密钥值。

未发现：

- 本地模型 Provider
- Ollama/vLLM/Transformers 集成
- 非 OpenAI-compatible 的独立 SDK Provider

### 5.2 能力矩阵

| 能力 | 状态 | 依据 |
| --- | --- | --- |
| 统一 Provider 接口 | 已实现 | `LLMClient` + Profiles + OpenAI-compatible client |
| 火山引擎 Ark | 已实现 | 默认 base URL 与三个 Ark profiles |
| OpenAI-compatible | 已实现 | Responses/Chat Completions 两种路径 |
| 结构化 JSON | 已实现 | json_schema/json_object/prompt_json |
| 本地 Schema 校验 | 已实现 | JSON Schema 2020-12 |
| JSON 修复 | 已实现 | Schema 不匹配时可额外修复一次 |
| 超时 | 已实现 | Profile 超时传入 SDK |
| 重试 | 已实现 | 指数退避、可重试错误映射 |
| 用量与请求诊断 | 已实现 | request ID、tokens、finish status、duration |
| 流式输出 | 未实现 | 所有调用均为同步 `create()` |
| Function calling/tools | 未实现 | 请求参数未包含 tools/tool_choice |
| Agent 工具循环 | 未实现 | 无 tool executor |
| 调用持久日志 | 部分 | 产物记录调用 metadata；无统一调用日志库 |
| Prompt 模板管理 | 分裂 | `agents/*` 文件模板与 Python 硬编码 prompt 并存 |

### 5.3 重复客户端判断

- 真实模型调用基本已统一到 `OpenAICompatibleLLMClient`。
- `MockLLMClient` 是测试/旧链兼容实现。
- `llm_mock.py` 另有招标解析、章节、审查等业务 mock。
- 没有发现第二套真实 OpenAI/Ark 客户端，但存在两套上层调用契约：
  - 旧 `generate_json`
  - 新 `generate_structured`

## 6. 企业知识库数据结构

### 6.1 存储层

| 层 | 位置 | 内容 |
| --- | --- | --- |
| 原始文件 | `storage/original/` | DOC/DOCX/XLSX 等入库源文件 |
| 转换/解析文件 | `storage/parsed/` | 转换文本或中间文件 |
| 图片资产 | `storage/assets/` | 文档图片、OCR资产 |
| Metadata 与结构化事实 | `data/metadata.json` | 文件记录、parsed_text、sections、tables、assets、resume_parse 等 |
| 项目状态 | `data/tender_projects.json` | 旧项目图、材料匹配和写作索引 |
| 向量接口 | `data/chroma/chroma.sqlite3` | Chroma collection 与 64 维向量 |
| 人工证据决策 | `data/manual_reviews/<lot>/evidence_resolution_decisions.json` | 证据确认、驳回、补材料、有效期等决策 |
| 项目证据输出 | `outputs/<lot>/evidence/` | evidence catalog、覆盖矩阵、复核队列 |

### 6.2 知识库清单

| 数据类型 | 存储位置 | 入库方式 | 查询方式 | 是否真实有数据 | 来源追踪 | 可复用性 |
| --- | --- | --- | --- | --- | --- | --- |
| 公司基本信息 | `metadata.json` | 未见专门解析器 | 无专门接口 | 未形成稳定结构化事实 | 弱 | 低 |
| 公司资质 | 原文件 + parsed tables + material_profile | ingest/parse/classify | 候选分组、表格证据提取 | 有 1 份实体材料，但 `company_qualifications` 结构化映射为空 | file_id、storage_key、表行 | 中 |
| 人员简历 | `files[].resume_parse`、`resumes` | parse-resume | query_personnel/evidence candidates | 有 2 人 | file_id、原文件、结构化字段 | 中到高 |
| 人员证书 | `resume_parse.certificates` | 简历解析/OCR | query_certificates | 有 17 条解析证书项 | file_id、asset/source 信息不完全一致 | 中 |
| 项目业绩 | `company_performances`、record 字段 | parse-company-performance | 关键词/候选查询 | 仅 1 条，来自明确 mock 测试文件 | file_id | 不可作为真实投标事实 |
| 合同 | 原始文件/材料分类 | 通用入库 | 候选查询 | 无独立企业合同包 | file_id 级 | 低 |
| 验收证明 | 原始文件/材料分类 | 通用入库 | 候选查询 | 未发现 | - | 低 |
| 产品材料 | material groups 预留 | 通用入库 | 候选查询 | 未发现真实产品资料 | - | 低 |
| 制造商材料 | evidence type 预留 | 通用入库 | 候选查询 | 未发现 | - | 低 |
| 服务网点 | 无专门事实结构 | 无 | 无专门接口 | 未发现 | - | 低 |
| 标准方案 | technical writing asset 预留 | 通用入库 | 候选查询 | 未发现 | - | 低 |
| 培训材料 | evidence type 预留 | 通用入库 | 候选查询 | 未发现 | - | 低 |
| 质量材料 | evidence type 预留 | 通用入库 | 候选查询 | 未发现 | - | 低 |
| 招标/评分/技术规范 | metadata + outputs | 解析/当前规划链 | 确定性解析与 ID 回查 | 有多份 | 文件、段落、表格、页码 | 高，但不属于企业证据 |

## 7. 企业知识库检索方式

### 7.1 分类结论

当前知识库实际属于：

- **A. 纯文件遍历或关键词匹配**
- **B. 结构化 JSON 查询**
- **G. 模拟数据或占位接口**

存在一个 **D. 向量检索的接口外形**，但不能认定为真实语义向量检索：

- `MockEmbeddingFunction` 使用 SHA-256 将文本映射到固定 64 维数值。
- 没有 embedding 模型调用或语义 embedding 生成。
- 查询向量与文档向量只是文本哈希映射，距离不代表语义相关度。

因此当前不是 C/E/F：

- 未发现应用层 BM25 或全文检索调用。
- Chroma 内部有 FTS 数据表，但项目代码没有使用其全文搜索接口。
- 无 hybrid search。
- 无 reranker。

### 7.2 功能核对

| 检索能力 | 状态 |
| --- | --- |
| embedding 模型调用 | 无 |
| embedding 生成 | 仅 mock hash |
| 向量存储 | 有 Chroma |
| 向量相似度查询 | 接口可执行，但语义结果不可信 |
| BM25/全文检索 | 无应用实现 |
| 混合检索 | 无 |
| reranker | 无 |
| metadata 过滤 | Chroma inspect 支持 file_id；search 不支持过滤 |
| 材料分组过滤 | 结构化候选查询支持 |
| 人员过滤 | query_personnel 与人员 profile 支持部分字段 |
| 部门过滤 | 人员列表/匹配模块支持；通用 KB query 未统一暴露 |
| 产品/制造商过滤 | 数据模型预留，实际数据和查询能力不足 |
| 证书有效期过滤 | 证据解析可提取并要求人工确认；查询接口未提供稳定过滤参数 |
| 原文件追踪 | 有 file_id、original_name、storage_key |
| 页码/表格定位 | 招标解析较完整；企业证据部分只有表行/近似位置 |
| 检索评分 | Chroma distance；结构化候选无统一相关度分 |
| 去重 | evidence catalog 有去重与冲突检测 |
| 引用片段 | parsed_text preview、matched_text、evidence excerpt；精度不一致 |

## 8. 当前真实数据情况

只读统计结果：

- `metadata.json` 文件记录：13
- 物理 `storage_key` 可找到：13/13
- 解析成功：12
- 有 parsed_text：12
- 有 sections：10
- 有 tables：8
- 有 assets：5
- 人员简历结构化记录：2
- 人员证书解析项：17
- 公司资质结构化映射：0
- 公司业绩结构化记录：1，但为 `mock_业绩文件_测试.docx`
- Chroma collection：1
- Chroma embedding 记录：13
- Chroma 维度：64
- Chroma 的 13 个分块全部来自同一份 `资质+代理证书.docx`

已有旧项目 evidence catalog：

- 来源文件：4
- 证据项：28
- 企业资质证据：9
- 人员简历：2
- 人员证书：16
- 公司业绩摘要：1（mock）
- 28 个证据项全部要求人工复核
- 支撑关系中 confirmed 数量：0
- evidence decision store 决策数：0

安全只读查询结果示例：

- `Python` 人员关键词查询能返回 1 条人员记录。
- 企业资质候选查询能定位 1 份物理材料。
- 人员简历候选查询能定位 2 份物理材料。
- 制造商资质候选为 0。

结论：

- 文件和部分结构化数据是真实存在的。
- 当前企业材料覆盖不足，无法支撑医院项目的产品、制造商、服务网点、业绩、培训、质量和绿色供应链等完整证据需求。
- 不能根据旧 evidence catalog 的 candidate 状态将任何材料视为已确认投标事实。

## 9. 已有工具和联网能力

### 9.1 工具清单

| 工具 | 路径 | 输入 | 输出 | 是否真实可用 | Agent可否直接调用 |
| --- | --- | --- | --- | --- | --- |
| 文件入库 | `file_store.py`、`kb_service.py` | 文件、分类、标签 | metadata record + 原始文件 | 可用，写操作 | 当前无 tool schema |
| 文档解析 | `document_parser.py` | metadata record | parsed_text/sections/tables/assets | 可用 | 可封装 |
| 文件拆分/分类 | `document_splitter.py`、`file_classifier.py` | parsed record | detected items/material profile | 规则可用 | 可封装 |
| Metadata 查询 | `metadata_store.py` | file_id/全量 load | JSON record | 可用 | 可封装为只读工具 |
| 企业知识查询 | `enterprise_knowledge_query.py` | query + metadata | certificates/personnel/qualifications/files | 可用但关键词能力有限 | 可直接包装 |
| 企业证据候选查询 | `query_enterprise_evidence_candidates()` | query、subject types、expected types | candidate records | 可用 | 最适合包装为写作工具 |
| Chroma search | `vector_store.py` | query、top_k | chunks + distance | 可执行但语义不可信 | 不建议直接给 Agent |
| 证据覆盖 | `evidence_coverage_builder.py` | 项目、requirements、metadata | catalog/matrix/review queue | 可用但默认写文件 | 不宜直接作为细粒度 Agent 工具 |
| 人工证据决策 | `evidence_resolution.py` | decision payload | 决策存储与状态变更 | 可用；支持 dry_run | 可作为人工确认接口 |
| 模型调用 | `llm_client.py` | prompts + JSON Schema | ModelCallResult | 可用 | 由编排器调用 |
| Markdown 渲染 | `chapter_drafting_agent.py`、`markdown_draft_writer.py` | 结构化章节 | Markdown | 可用 | 编排器内部使用 |
| DOCX表格填充 | `simple_special_table_filler.py` | 模板 + metadata | 表格 DOCX | 可用，范围有限 | 不应在正文阶段直接调用 |
| DOCX合并 | `technical_doc_composer.py` | 主文档 + 表格文档 | 合成 DOCX | 可用，只有两个占位符 | 非当前章节 Agent 工具 |
| 网联需求计划 | `technical_section_task_planner.py` | task | web plan | 只规划 | 不是真实联网工具 |

### 9.2 工具体系判断

未发现：

- 工具注册表
- Agent tool JSON schema
- function calling
- MCP
- LangChain tools
- LangGraph
- 自定义 tool executor
- Agent 工具调用循环

当前工具编排是普通 Python 函数的显式顺序调用。

### 9.3 联网能力

未发现搜索引擎、浏览器、网页抓取、URL正文提取、来源白名单执行器或搜索引用归档。

已有内容仅限：

- `web_policy`
- `web_research_plan.json`
- allowed source types
- 外部引用待人工解析状态

模型 Provider 的 HTTP 请求不等同于联网检索工具。

结论：当前联网能力不能直接服务技术写作 Agent。

## 10. 技术标现有链路

### 10.1 当前产物状态

医院项目：

- 一级章节：8
- 全部文章节点：70
- 原子任务：63
- draft units：25
- response point：31/31
- ★响应点：3/3

`writing_requirements` 已进入全部文章节点，包含：

- response_point_ids
- score_item_refs
- required_content_points
- manual_project_facts
- local_kb
- web
- draftability

### 10.2 十项判断

1. **`technical_agent_context_loader` 输出能否直接作为现有 Agent 输入？**

   不能直接输入旧 AgentRunner 或旧 `build_technical_draft()`。
   旧章节 Agent 需要六个文件并强制九章；新上下文是单章最小结构。

2. **`writing_requirements` 是否足够指导工具调用？**

   足够作为“是否需要人工事实、本地材料、联网”的路由依据；不足以直接执行检索，因为没有实体值、过滤参数、top_k、证据最小字段和查询结果契约。

3. **response point 如何回查招标原文？**

   `technical_agent_context_loader.py:31` 读取节点 `response_point_ids`，在 response outline 中按 `point_id` 建索引，再取 `original_text`、表格和来源引用。

4. **大型参数表如何局部加载？**

   普通章节只返回 `table_access`，不内嵌整表；三个产品小节通过 `_product_table_excerpt()` 按产品实体切片。

5. **是否已有可复用章节生成能力？**

   有：统一模型调用、章节 JSON Schema、AI chapter 生成、事实边界校验、覆盖检查、Markdown 渲染。

6. **是否已有可复用章节审查能力？**

   有部分确定性校验；`chapter_review` Prompt 和 `ReviewService` 尚未形成真实模型审查链。

7. **哪些组件与拟议 `TechnicalWritingAgent` 重复？**

   `chapter_drafting_agent`、`chapter_drafting_pilot`、`LLMClient`、章节 Schema、事实边界/覆盖校验、Markdown 渲染已覆盖大部分模型生成内核。

8. **当前上下文是否控制大型内容？**

   是。WC-003 只含实施、交货、验收、人员相关响应点，不含 TR-018 参数大表。

9. **draft units 是否需要暴露？**

   不需要。它们只是内部批次建议，新加载器已声明不依赖 draft unit。

10. **是否已有医院项目正文生成结果？**

    没有。医院项目输出只有大纲、任务、规划和 WC-003 上下文。

### 10.3 技术写作复用矩阵

| 当前所需能力 | 仓库现有实现 | 完成度 | 建议 |
| --- | --- | ---: | --- |
| 章节上下文加载 | `technical_agent_context_loader.py` | 90% | 直接复用 |
| 模型调用 | `LLMClient`、`OpenAICompatibleLLMClient`、Profiles | 85% | 直接复用，先控制单章输入和输出预算 |
| 本地知识库查询 | `enterprise_knowledge_query.py`、`MetadataStore` | 55% | 包装成只读工具；暂不使用 mock 向量结果 |
| 联网搜索 | 只有 web plan 与 source policy | 10% | 当前标记 unavailable，后续单独建设 |
| 项目事实读取 | context loader 有可选路径；没有正式 facts 存储 | 30% | 增加小型项目事实文件或等价适配器 |
| 章节正文生成 | `chapter_drafting_agent.py`、Pilot | 60% | 复用生成内核，不复用旧九章输入外壳 |
| 覆盖检查 | response point coverage、章节 draft validator | 80% | 直接复用并适配 WC/section ID |
| 事实审查 | claim boundary、evidence status、人工决策仓 | 60% | 复用确定性校验；真实审查模型仍缺 |
| Markdown保存 | 章节 renderer、markdown writer | 85% | 直接复用章节 renderer |
| DOCX装配 | 特殊表填充、双占位符合成器 | 35% | 本阶段不接入，后续独立扩展 |

## 11. 可直接复用的组件

| 组件 | 复用方式 |
| --- | --- |
| `technical_agent_context_loader.py` | 作为章节输入裁剪入口 |
| `technical_writing_outline.json` 的 `writing_requirements` | 工具路由和 draftability 判断 |
| `LLMClient` / `OpenAICompatibleLLMClient` | 正式模型调用 |
| `llm_profiles.py` | drafting/review/fast_task 模型选择 |
| `chapter_draft_output_schema.json` | 章节结构化输出契约 |
| `chapter_drafting_agent.build_ai_chapter()` 的思路 | 模型调用、输出归一化与失败对象 |
| `validate_chapter_draft()` | 覆盖、占位符、企业事实边界校验 |
| `query_enterprise_evidence_candidates()` | 本地企业材料候选查询 |
| `MetadataStore.load/get_file()` | 只读事实与原文件索引 |
| `evidence_resolution.py` | 证据人工确认与状态 |
| `render_chapter_markdown()` | 章节 Markdown 输出 |
| response point/source refs | 招标原文和页码追踪 |

应复用“底层能力”，不应照搬旧九章中间文件体系。

## 12. 重复、废弃或实验性组件

1. `AgentRunner + agents/*` 与 `chapter_drafting_agent` 是两套不同 Agent 契约。
2. `writing_orchestrator`、`writing_context_compiler`、当前 `technical_writing_outline` 规划链是三代写作规划实现。
3. `llm_mock.py` 中 tender/section/review 实现仍被部分 CLI 调用，容易被误认为真实 Agent。
4. `agents/kb_search`、`chapter_review`、`full_review`、`revision` 目前主要是 Prompt 配置。
5. `agents/document_splitter`、`response_matrix`、`score_table_parser_agent` 的实际工作由确定性代码完成。
6. `vector_store.py` 名称像 RAG，但实际是 mock embedding。
7. `markdown_draft_writer.build_technical_draft()` 与 `chapter_drafting_agent.build_technical_draft()` 同名但职责不同。
8. 旧章节 Agent 硬编码九章和特定 `technical_*` score ID，不适合作为通用医院项目入口。
9. 双阶段 Pilot 的输出目录固定为 `pilot/service_solution`，也明显是试验性实现。
10. `company_master_outline_v0.broken.json` 是备份，不应参与运行时发现。

## 13. 当前缺口

1. 缺少当前最小章节上下文到章节生成内核的适配器。
2. 缺少可由写作编排器调用的只读 KB 工具契约。
3. 缺少真实 embedding、有效向量索引和语义检索评测。
4. 缺少制造商、产品、检测报告、参数证明、服务网点等医院项目数据。
5. 缺少 per-project 已确认品牌型号、实际参数、团队、承诺和偏离事实。
6. 缺少联网搜索执行器和官方来源策略执行。
7. 缺少正式章节审查模型链。
8. 缺少成功的真实章节模型端到端产物；现有真实 Pilot 因长度截断失败。
9. 旧提示词与代码内 Prompt 并存，管理方式不统一。
10. 缺少真实 KB 集成测试；当前 pytest 主要覆盖合成数据和 fake SDK。

## 14. 知识库成熟度结论

**结论：L2——结构化事实查询可用，但属于有限 L2。**

依据：

- 文件入库、解析、metadata、原文件追踪均已实现。
- 人员简历和证书可结构化查询。
- 材料分组、证据候选、证据目录、去重、人工复核状态已实现。
- 真实企业数据覆盖很小，资质未形成稳定结构化 facts，业绩仅为 mock。
- Chroma 使用 mock embedding，不能算基础 RAG。
- 尚未达到 L3，更不具备 L4/L5 的混合检索、稳定过滤、重排和生产级证据召回。

## 15. Agent 成熟度结论

**结论：A2——单步模型调用可用。**

依据：

- 统一真实模型 Provider、Schema 输出、重试、超时和错误诊断已实现。
- 章节生成函数和确定性校验已实现。
- 真实 Provider 曾返回模型响应，说明网络调用链可以到达 Provider。
- 现有真实章节 Pilot 最终失败，尚无稳定成功样例。
- Agent 没有工具调用/function calling/工具循环，因此不是 A3。
- 当前医院项目没有章节级生成和审查工作流，因此不是 A4/A5。

## 16. 推荐接入方案

**选择：方案B——复用模型和工具层，新建轻量技术写作编排器。**

推荐调用路径：

```text
response outline path
+ writing outline path
+ optional confirmed facts path
+ target chapter/section ID
→ technical_agent_context_loader
→ 读取 writing_requirements
→ 显式调用只读项目事实/企业证据工具
→ 必要时调用未来 web tool
→ 组装单章 prompt
→ LLMClient.generate_structured
→ 复用章节 Schema 与确定性校验
→ 保存结构化章节和 Markdown
```

不建议采用方案A：

- 没有一个现有 Agent 能直接接收新上下文并自主调用工具。

不建议采用方案C：

- 不需要重做全部 Agent 层；模型调用、章节 Schema 和校验已具备复用价值。

不建议采用方案D：

- 现有解析、规划、模型和结构化知识查询不是不可用。

是否需要新增 `TechnicalWritingAgent`：

- 不需要完整自治、多 Agent 或 LangGraph Agent。
- 需要一个轻量、显式步骤、章节级的 orchestrator。
- 该编排器应把工具调用保持为普通受控函数，不必为了“Agent”名称引入 function calling。

## 17. 最小改动文件清单

建议下一轮只涉及 **4 个代码/测试文件**：

1. 新增 `app/technical_writing_orchestrator.py`
   - 接收两个正式 outline、可选 confirmed facts、chapter/section ID。
   - 调用 context loader、工具包装、模型和校验。
2. 新增 `app/technical_writing_tools.py`
   - 只读包装 `query_enterprise_evidence_candidates()`、project facts 读取。
   - 明确禁止写入、禁止把 tender 最低值转为投标事实。
   - 联网接口先定义协议或返回 unavailable，不执行搜索。
3. 修改 `cli.py`
   - 增加单章 dry-run/生成命令；不暴露 task ID 或 draft unit ID。
4. 新增 `tests/test_technical_writing_orchestrator.py`
   - fake LLM、fake KB tool、无联网、无正文事实编造、覆盖保持测试。

第一阶段不需要修改：

- `llm_client.py`
- `openai_compatible_llm_client.py`
- `technical_agent_context_loader.py`
- `technical_response_outline.json`
- `technical_writing_outline.json`
- `vector_store.py`
- DOCX 代码

### `confirmed_project_facts.json` 判断

- 现有 evidence decision store 可复用来保存“某份证据是否确认、有效期、主体、人员可投入性”等决策。
- 它不能表示拟投品牌型号、实际配置、项目团队选择、交货承诺、服务承诺和技术偏离。
- 因此仍需要一个很小的 per-project confirmed facts 数据源，或者为 `tender_projects.json` 增加等价的项目事实节点。
- 不需要新建大型统一 Agent 输入文件或另一套工作流状态系统。
- 为保持上下文加载器的文件接口和项目隔离，独立的小型 `confirmed_project_facts.json` 比继续膨胀全局 `tender_projects.json` 更安全。

## 18. 不建议做的事情

1. 不要重写统一模型 Provider。
2. 不要把旧九章 `writing_context_pack/chapter_task_packets` 强行转换成医院八章目录。
3. 不要把 `AgentRunner` 的 mock 结果升级标签后当作正式正文。
4. 不要把 Chroma 当前 distance 当作语义相关度。
5. 不要在真实 embedding 未完成前宣称已有 RAG。
6. 不要让 Agent 直接读取整个 `metadata.json` 或 211 行参数表。
7. 不要将 candidate/needs_review 材料写成 confirmed fact。
8. 不要从 mock 业绩文件生成医院项目业绩事实。
9. 不要立即引入 LangChain、LangGraph、MCP 或复杂多 Agent 框架。
10. 不要在技术正文 Agent 阶段同时建设 DOCX。
11. 不要用互联网补全本公司人员、资质、业绩、承诺或实际产品配置。
12. 不要另建大型 `technical_agent_input.json`。

## 19. 下一轮建议任务

建议下一轮只做“单章节轻量编排器 dry-run”：

1. 以 WC-003 为目标，读取现有最小上下文。
2. 只读加载可选 confirmed facts；缺失时保留占位符。
3. 根据 `local_kb.evidence_types` 生成并执行受控的结构化候选查询。
4. 将候选材料保持 candidate，不自动确认。
5. 不执行联网；将 web requirements 原样保留为未执行需求。
6. 用 fake LLM 验证 prompt、Schema、覆盖和事实边界。
7. 复用现有章节校验函数。
8. 只输出结构化章节草稿与 Markdown 测试产物，不生成 DOCX。

通过后，再单独决定：

- 是否接真实模型做一个 WC-003 小节试点；
- 是否先补全医院项目产品/制造商证据；
- 是否将 mock embedding 替换为真实 embedding 和经过评测的检索。

---

## 附：测试与安全审计结果

执行的无缓存、无联网相关测试：

```text
tests/test_agent_runner.py
tests/test_agent_draft_generator.py
tests/test_llm_provider_layer.py
tests/test_chapter_drafting_agent.py
tests/test_writing_context_compiler.py
tests/test_technical_agent_context_loader.py
tests/test_evidence_coverage_builder.py
tests/test_evidence_resolution.py
tests/test_material_matcher.py
```

结果：

```text
166 passed in 7.44s
```

测试性质：

- 模型 Provider 测试使用 fake SDK，不是在线 Provider 集成测试。
- 章节 Agent 测试主要使用 injected client、preview 或临时目录。
- AgentRunner 测试验证的是 mock 路径。
- 正式 pytest 中没有覆盖真实 embedding 或真实 Chroma 语义召回质量的测试。

安全检查：

- `.env` 和 `llm_profiles.local.json` 已在 `.gitignore` 中。
- 配置使用环境变量读取 API key 和模型名。
- 未发现硬编码生产密钥；发现的密钥样式命中为测试假值或 ID 字符串误匹配。
- 未发现明确运行时循环依赖；本次相关测试全部通过。
- 最大风险不是密钥，而是 mock/preview/真实能力命名接近，容易误用。
