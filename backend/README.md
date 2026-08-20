# comlan智标 Backend

当前项目同时保留部分历史 mock/rule-based 解析服务，并已建立基于模型 Profile 的真实大模型接入层。技术章节撰写主线不会自动使用 mock；真实模型不可用时必须显式选择 `--preview` 或 `--dry-run`。

## 招标文件 + 评分表 + 指定标段解析流程

用于处理实际项目中“招标文件”和“评分表”分离，且评分表包含多个标段/包件/采购包的场景。当前阶段由用户手动指定投标标段，系统按指定标段生成项目级 Tender Task Graph。

### 1. 上传并解析招标文件

```bash
python cli.py ingest-file ".\samples\招标文件.docx" --category 招标文件 --tags 招标文件
python cli.py parse-file <tender_file_id>
python cli.py parse-tender <tender_file_id>
```

### 2. 上传并解析评分表

```bash
python cli.py ingest-file ".\samples\评分表.docx" --category 评分表 --tags 评分表
python cli.py parse-file <score_file_id>
python cli.py parse-score-table <score_file_id> --lot-name "第一标段"
```

`parse-score-table` 会调用 `agents/score_table_parser_agent`，输出指定标段下的商务评分项、技术评分项、价格评分项、资格/符合性审查项、废标或强制要求、得满分所需证明材料、知识库匹配提示和标书章节生成任务。

### 3. 合并项目任务图

```bash
python cli.py parse-tender-project --tender-file-id <tender_file_id> --score-file-id <score_file_id> --lot-name "第一标段"
```

`parse-tender-project` 会调用 `agents/tender_merge_agent`，将招标文件解析结果、评分表解析结果和指定标段合并为 Tender Task Graph，并写入 `data/tender_projects.json`。

当前阶段仍为 mock/rule-based 实现，后续可替换为真实 LLM 调用。

这是 `comlan智标` 的后端 mock 工程。当前阶段不接前端，不提供完整 API，先通过 CLI 验证知识库、资料拆分、招标解析、标书生成和审查能力。

## 安装

```bash
cd backend
pip install -r requirements.txt
```

Python 版本建议 3.10+。

## 大模型 Profile 配置

模型 Profile 将业务角色与具体 Provider、模型、API 模式、能力和请求映射分离。`TechnicalWritingAgent` 与其他写作入口只依赖统一 `LLMClient`，不直接依赖火山、DeepSeek、OpenAI SDK 或具体模型 ID。

项目提供三个逻辑角色：

- `drafting`：章节撰写，默认绑定 `ark_draft`
- `review`：后续正文复核，默认绑定 `ark_review`
- `fast_task`：轻量分类、摘要和格式任务，默认绑定 `ark_fast`

提交到仓库的示例配置位于：

- `config/llm_profiles.example.json`
- `.env.example`

本地开发时可创建 `.env.local` 或 `.env`。加载优先级为当前进程环境变量、`.env.local`、`.env`、Profile 非敏感默认值。真实 Key 不得写入示例文件或 Profile JSON：

```dotenv
DEEPSEEK_API_KEY=
ARK_API_KEY=
COMLAN_ARK_DRAFT_MODEL=
COMLAN_ARK_FAST_MODEL=
COMLAN_ARK_REVIEW_MODEL=

COMLAN_LLM_DRAFT_PROFILE=deepseek_draft
COMLAN_LLM_REVIEW_PROFILE=ark_review
COMLAN_LLM_FAST_PROFILE=ark_fast
COMLAN_WEB_SEARCH_PROFILE=ark_builtin
```

`deepseek_draft` 与 `deepseek_fast` 已在 Profile 中分别使用非敏感模型名 `deepseek-v4-pro` 和 `deepseek-v4-flash`，通常只需设置 `DEEPSEEK_API_KEY`。火山模型变量填写方舟控制台提供的模型或推理接入点 ID。可选的 `ARK_BASE_URL` 用于覆盖 Profile 中的方舟兼容地址。

如需覆盖示例 Profile，可自行创建被 Git 忽略的 `config/llm_profiles.local.json`，或通过 `COMLAN_LLM_PROFILE_CONFIG` 指向本地覆盖文件。加载顺序为示例配置、本地覆盖、环境变量。

### 查看 Profile

```bash
python cli.py list-llm-profiles
```

命令只显示 Key/模型是否已配置，不显示 Key、Key 前缀或 Key 长度。

### 检查火山方舟连通性

```bash
python cli.py check-drafting-model --profile ark_draft --smoke-mode minimal --no-write
python cli.py check-drafting-model --profile ark_draft --smoke-mode chapter-schema --no-write
python cli.py check-drafting-model --profile ark_draft --smoke-mode chapter-schema --thinking enabled --timeout-seconds 180 --no-write
python cli.py check-drafting-model --profile ark_draft --smoke-mode chapter-schema --thinking enabled --timeout-seconds 600 --max-output-tokens 4000 --no-write
```

`minimal` 使用极小 JSON Schema，只检查真实调用和本地 JSON 解析，固定采用 60 秒超时、关闭深度思考且不执行 Schema 修复。这里的 60 秒只是 minimal smoke test 的本地检查超时，不是火山方舟或 Responses API 的平台限制。

`chapter-schema` 使用正式的 `agents/technical_writer/chapter_draft_output_schema.json`，默认采用 120 秒超时、1200 个输出 token 和关闭深度思考，可通过 `--thinking`、`--timeout-seconds` 与 `--max-output-tokens` 临时覆盖。深度思考可能消耗较多输出预算；低预算下若 Provider 返回 incomplete 且没有最终文本，检查结果会保留 request ID、usage、incomplete details 和输出项类型，不会误报为 Schema 校验失败。所有 smoke 参数只存在于当前进程内，不写回正式 Profile 或 `llm_profiles.local.json`。默认检查结果写入 `outputs/model_checks/latest_drafting_model_check.json`，不会修改任何真实 case 草稿。缺少 Key 或模型 ID 时返回 `configuration_missing`，不会伪造成功。

正式章节生成始终使用所选 Profile 自己的 `timeout_seconds`、`max_retries`、`max_output_tokens` 和 thinking 配置。长章节启用深度思考时，可在正式 Profile 中将超时调整为 180～300 秒；smoke test 超时不等于真实写作不可用。

### 两段式单章 Pilot

阶段 5B 的 pilot 先由 `drafting` 角色生成轻量章节正文，再由 `fast_task` 角色关闭深度思考并整理为正式章节 Schema。两个阶段是独立模型调用；pilot 只写入 `outputs/<lot_name>/drafts/pilot/service_solution/`，不会更新九章草稿或合并稿。

```bash
python cli.py build-technical-draft-pilot --lot-name <lot_name> --chapter-id <chapter_id>
python cli.py inspect-technical-draft-pilot --lot-name <lot_name>
```

可通过 `--draft-profile` 和 `--normalize-profile` 选择其他已配置 Profile，不直接传模型 ID。测试建议：

- `ark_draft`：thinking enabled，`timeout_seconds` 可设为 600，`max_output_tokens` 建议 12000～16000；仅对网络、限流和服务端临时错误重试。
- `ark_fast`：thinking disabled，`timeout_seconds` 可设为 180，`max_output_tokens` 建议 4000～6000，temperature 使用 0 或较低值。

两个 Profile 可以暂时使用相同模型 ID。章节任务预算分为 short=8000、standard=12000、long=16000；实际调用取任务建议与正式 Profile 上限中的较小值，不会静默突破 Profile。Schema 错误或长度截断不会触发无约束重复撰写，第一段失败时也不会调用第二段或回退 mock/preview。

### Preview、Dry-run 和真实模式

```bash
# 仅组装章节任务和 Prompt，不调用模型、不写草稿
python cli.py build-technical-draft --lot-name <lot_name> --dry-run

# 显式生成确定性测试预览，不调用真实模型
python cli.py build-technical-draft --lot-name <lot_name> --preview

# 使用 drafting 角色绑定的真实 Profile
python cli.py build-technical-draft --lot-name <lot_name>

# 使用指定 Profile 覆盖 drafting 角色默认值
python cli.py build-technical-draft --lot-name <lot_name> --profile ark_draft
```

真实调用失败不会自动回退 `MockLLMClient` 或 deterministic preview。`MockLLMClient` 只用于单元测试，不能生成 `draft_mode=ai_generated`。

### API 模式与结构化输出

OpenAI 兼容客户端支持：

- `api_style`（兼容旧字段 `api_mode`）：`responses`、`chat_completions`
- `structured_output_mode`: `json_schema`、`json_object`、`prompt_json`
- `capabilities`：tools、streaming、developer role、tool choice、reasoning content/replay 等能力
- `request_mapping.system_role`：`system`、`developer`
- `request_mapping.token_limit_parameter`：`max_tokens`、`max_completion_tokens`、`max_output_tokens`
- `request_mapping.finalization_strategy`：`omit_tools`、`tool_choice_none`

无论 Provider 是否原生支持 JSON Schema，返回结果都必须通过同一份本地章节 Schema。首次结构校验失败最多执行一次受控修复，第二次失败即返回 `ModelSchemaValidationError`。

### 增加其他 OpenAI 兼容 Provider

在本地 Profile 中增加新的 `provider`、`base_url`、`api_key_env`、`model`/`model_env`、`api_style`、`capabilities` 和 `request_mapping` 即可。增加兼容 Provider 不需要修改章节撰写业务代码、工具注册或写作规则。主模型 Profile 与 `COMLAN_WEB_SEARCH_PROFILE` 独立；例如主模型可使用 `deepseek_draft`，Web Search 仍由 `ark_builtin` 及 `ARK_API_KEY` 提供。

纯离线 fake-client 演示：

```bash
python scripts/offline_model_profile_demo.py
```

演示还覆盖模型 JSON 容错链：工具结果回填后若最终 JSON 仅有代码块或简短解释，
由确定性解析器直接提取；若 JSON 轻微损坏或截断，则使用同一模型执行最多一次无工具
repair。失败原文只会以脱敏副本写入 `outputs/<output_key>/debug/`，运行报告仅记录路径和
SHA-256；未取得章节对象时会跳过重复内容自检和章节业务校验。

真实章节入口保持不变：

```bash
python cli.py run-technical-writing-agent ... --model-profile deepseek_draft --web-search-provider ark_builtin
```

## 基础命令

### 人工筛选文件生成技术标总响应大纲

将当前标包已经人工筛选确认的招标文件、项目需求书、技术规范书、技术评分表、技术文件格式及技术附件放入同一目录，然后执行：

```bash
python cli.py build-technical-outline --input-dir "<当前标包有效文件目录>"
```

默认以输入目录名作为 `outputs/<目录名>/` 的输出键；如需只调整输出目录名称，可显式使用 `--output-key`，该参数不参与标包识别：

```bash
python cli.py build-technical-outline --input-dir "<有效文件目录>" --output-key "<输出目录名>"
```

正式产物包括：

- `outputs/<输出键>/outline/technical_response_outline.json`
- `outputs/<输出键>/outline/technical_response_outline.md`
- `outputs/<输出键>/outline/technical_response_outline.preview.html`

JSON 是统一结构化事实。JSON 保留供 Agent 使用的原子响应点，并派生面向人工阅读的 `display_blocks`；Markdown 和 HTML 预览使用同一组展示区块，不形成第二套业务数据源。该命令复用现有 DOCX、PDF、Excel、PPT 和文本解析器，不递归解压压缩包，不识别或选择标包，不调用企业知识库、材料匹配、评分行动分析或大模型，也不生成技术正文。提取层保留原文、编号、强制标记、表格和具体来源，只合并内容完全相同或仅空白排版不同的响应点。

VS Code 的 Markdown 普通编辑视图会显示嵌入表格的 HTML 源码。使用 `Ctrl+Shift+V` 或 `Ctrl+K V` 打开 Markdown 预览；包含 `rowspan`、`colspan` 的复杂合并表格，建议直接打开 `technical_response_outline.preview.html` 查看。

```bash
python cli.py ingest-file ./samples/ISO9001.pdf --category 企业资质库 --tags ISO,质量管理
python cli.py parse-file <file_id>
python cli.py classify-file <file_id>
python cli.py index-file <file_id>
python cli.py search-kb "ISO9001 有效期"
python cli.py parse-tender ./samples/招标文件.pdf
python cli.py generate-section --section 售后服务方案 --query "售后服务 培训 响应时间"
python cli.py review-section --section 售后服务方案
```

## 自动入库识别

`ingest-auto` 支持单个文件或文件夹。上传后不会直接正式入库，而是生成 `pending_items` 待确认条目。用户确认后，`index-file` 才会优先按 confirmed pending item 写入 ChromaDB。

### 单文件自动识别

```bash
python cli.py ingest-auto ".\samples\ISO9001.pdf"
python cli.py list-pending-items
python cli.py confirm-item <item_id>
python cli.py index-file <file_id>
```

### 文件夹批量识别

```bash
python cli.py ingest-auto ".\samples\资质资料"
python cli.py list-pending-items
```

### 汇总文档自动拆分

```bash
python cli.py ingest-auto ".\samples\公司资质汇总.docx"
python cli.py list-pending-items
```

自动识别流程：

1. 保存源文件到 `storage/original/`
2. 执行 mock 文档解析和资产生成
3. 根据文件名、`parsed_text` 和资产 OCR 文本执行规则分类
4. 判断是否为汇总资料
5. 单资料生成 1 个 pending item
6. 汇总资料先拆分，再为每个拆分条目生成 pending item

当前分类规则：

- 包含“营业执照” → 企业资质库 / 营业执照
- 包含“ISO9001”或“质量管理体系认证” → 企业资质库 / 体系认证
- 包含“ISO27001”或“信息安全管理体系认证” → 企业资质库 / 体系认证
- 包含“AAA”或“信用等级” → 企业资质库 / 信用证书
- 包含“专利” → 企业资质库 / 专利证书
- 包含“软件著作权”或“软著” → 企业资质库 / 软件著作权
- 包含“合同” → 业绩案例库 / 合同
- 包含“中标通知书” → 业绩案例库 / 中标通知书
- 包含“验收报告” → 业绩案例库 / 验收报告
- 包含“产品手册”或“技术参数” → 产品资料库 / 产品手册
- 包含“检测报告” → 产品资料库 / 检测报告

若命中多个关键词，或 Word/PDF 文件生成的 assets 数量大于 2，则 `is_bundle = true`，系统会自动执行拆分逻辑。

## 资料拆分入库流程

用于处理 `公司资质汇总.docx` 这类一个文件内包含多个资质、证书、合同、专利、检测报告的场景。

```bash
python cli.py ingest-file ".\samples\公司资质汇总.docx" --category 企业资质库 --tags 资质汇总
python cli.py parse-file <file_id>
python cli.py split-file <file_id>
python cli.py list-split-items <file_id>
python cli.py confirm-split-item <file_id> <item_id>
python cli.py index-file <file_id>
python cli.py search-kb "ISO9001 质量管理体系认证"
```

`split-file` 会读取 `metadata.json` 中已解析的 `parsed_text` 和 `assets`，生成 `detected_items`。每个条目包含：

- id
- source_file_id
- name
- category
- document_type
- tags
- valid_from
- valid_to
- source_page
- source_text
- related_asset_ids
- recommended_usage
- confidence
- status: pending / confirmed / ignored

当前拆分逻辑是 mock + 基础规则：

- 包含“营业执照”生成营业执照条目
- 包含“ISO9001”或“质量管理体系认证”生成 ISO9001 条目
- 包含“ISO27001”或“信息安全管理体系认证”生成 ISO27001 条目
- 包含“AAA”或“信用等级”生成 AAA 信用等级证书条目
- 包含“专利”生成专利证书条目
- 包含“软件著作权”或“软著”生成软件著作权条目
- 包含“检测报告”生成检测报告条目
- 包含“合同”生成合同条目
- 未识别到内容时生成“未分类资料”

## index-file 规则

- 如果文件存在 `confirmed` 的 `pending_items`，优先将每个 confirmed pending item 作为独立知识库条目写入 ChromaDB。
- 如果没有 confirmed pending items，但存在 `confirmed` 的 `detected_items`，则按 detected items 入库。
- 如果都没有，仍按原始文件 `parsed_text` 入库。

## 结构化事实落盘位置

当前不单独建立企业数据库，结构化事实继续落在 `data/metadata.json`：

- 简历解析结果写入 `files[].resume_parse`，并同步写入 `metadata.resumes[file_id]`。
- 企业资质解析结果写入 `files[].company_qualification`，并同步写入 `metadata.company_qualifications[file_id]`。
- 企业资质解析结果包含 `requirements[]`、`source_file_id`、`original_name`、`parsed_at`、`parser_version`。
- OCR 结果保留在 `files[].assets[].ocr_text`、`files[].assets[].ocr_status`、`files[].assets[].ocr_confidence`。
- `query-knowledge` 优先读取已落盘的结构化事实；如果某个文件还没有 `company_qualification`，才从该文件 `parsed_text` 临时解析 CMMI/DCMM 要求。

## 当前限制

当前仍存在以下限制：

- 火山方舟真实调用需要用户配置 `ARK_API_KEY` 和模型 ID
- 不解析真实 PDF/Word 正文
- 不生成真实页面截图
- Chroma 使用本地持久化目录和 mock embedding

`llm_mock.py` 仍被部分历史文件解析、旧章节生成和复核服务使用，本轮未统一清理。后续可逐步将这些服务迁移到 Profile 模型层。
