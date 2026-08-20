# 企业证据人工决策录入说明

> 本文件只用于人工操作说明，不参与证据匹配、状态判断或下游计算。

## 使用边界

- 决策模板：`outputs\0425139003001-6582\evidence\evidence_review_decision_template.json`
- 正式决策存储：`data/manual_reviews/0425139003001-6582/evidence_resolution_decisions.json`
- 模板包含 10 个核查事项和 8 个决策槽。两张 ISO 证书各自的身份与有效期核查共用一个真实 resolution_id，请在同一决策中一次填写。
- `current_facts`、原摘录或解析值只是核对线索，不代表人工确认结果。不得直接把空字段批量替换为解析值。

## decision 含义

- `confirm`：人工确认指定事实；仍需通过对应证据类型的最低字段门槛，才能成为 confirmed。
- `partially_confirm`：只确认部分字段，未满足最低门槛时保持 candidate 或 needs_review。
- `reject`：证据或支撑关系错误、不适用、过期、重复或主体不一致。必须填写具体 `reason`。
- `needs_more_material`：现有材料不足。必须填写 `reason` 和 `field_updates.requested_materials`。
- `not_applicable`：该复核对象对当前范围不适用。
- `defer`：暂缓处理，不改变证据为 confirmed。

每条决策必须填写：`decision_id`、`decision`、`reason`、`reviewer`、`decided_at`、`verification_method`。`decided_at` 使用带时区的 ISO 8601 时间。更新已有决定时填写 `supersedes_decision_id`，不要删除历史决定。

## 最低确认字段

ISO 管理体系证书：证书名称、证书编号、持证主体、有效期或明确有效状态、适用范围，以及与当前证据需求的对应关系。模板中的有效期解析值必须由人工对照来源后重新填写或明确确认。

人员：姓名、人工确认的部门、可投入状态、拟任角色或用途；相关简历、经历和证书仍需各自来源。部门、可投入性和角色是三个独立决定，不要用一个字段替代另两个事实。

图片证书视觉确认：使用 `verification_method=human_visual_review`，保留 `visual_source_ref`，填写可辨认的证书名称、持证人、编号、发证机构、有效期或状态；无法辨认的字段写入 `unreadable_fields`。不得编造文本 `excerpt`。

## 驳回和补材料

`reject` 的 `reason` 应说明具体原因，例如材料为测试文件、主体错误、证据类型错误、过期、重复或不支撑指定要求。模板对 mock 材料仅给出建议决定，仍须人工核查并填写理由。

`needs_more_material` 应在 `reason` 中说明现有证据缺什么，并在 `field_updates.requested_materials` 中列出可执行的补录组件。企业业绩证据包应按独立项目准备合同关键页、可用的中标通知书、验收或用户证明，以及项目名称、客户、时间、金额、工作内容和相关性说明。

## 校验与导入

先执行只读校验；该命令不写入决策存储，也不重建覆盖矩阵：

```powershell
python cli.py apply-evidence-review-decisions --lot-name 0425139003001-6582 --input "outputs\0425139003001-6582\evidence\evidence_review_decision_template.json" --dry-run
```

确认 dry-run 的 schema、ID、枚举、最低字段和 supersede 检查结果后，再去掉 `--dry-run` 正式导入：

```powershell
python cli.py apply-evidence-review-decisions --lot-name 0425139003001-6582 --input "outputs\0425139003001-6582\evidence\evidence_review_decision_template.json"
```

正式导入会保存人工决定并重建证据覆盖。随后检查：

```powershell
python cli.py inspect-evidence-resolution-queue --lot-name 0425139003001-6582
python cli.py inspect-evidence-coverage --lot-name 0425139003001-6582
```

Markdown 仅供人工阅读，业务计算仍以正式 JSON 和持久化决策存储为准。
