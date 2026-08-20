# 全文机器审计报告

> Stage ⑪-D 全文机器审计确定性收口已完成。
> Model finding 只有经过 Runtime policy 晋升后才构成正式 FAI。

## 1. 审计结论

- status：completed
- audit_result：blocked
- release_ready：false
- Deterministic Audit：completed
- Semantic Audit：completed
- Finalization：completed
- Stage ⑪-D 模型调用：0

## 2. 正式全文问题

- FAI 总数：7
- fatal：0
- blocking：1
- warning：6
- info：0

### blocking

#### FAI-8A16A33D982E3BF8 · coverage_content_mismatch

- Subject：TR-003
- Chapter：WC-005
- Section：WC-005-11
- Requirement：TR-003
- Source FSF：FSF-A6B496F300D1758B
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：主位置“服务机构与人员”为空，外部“服务承诺”仅泛泛承诺将按要求提供信息，未给出实质性内容。
- 判定依据：TR‑003要求提供制造商服务机构的地址、联系方式及技术人员数量。计划主位置WC‑005‑04内容完全为空，未提供任何信息。唯一可见的引用位置WC‑005‑11仅表示“依据招标文件要求提供”，未包含具体数据，属于缺乏实际内容的通用承诺。根据规则，此情形为coverage_content_mismatch。
- 建议处理：请在WC‑005‑04或其它适当位置填入制造商服务机构的详细地址、联系电话及技术人员数量。

### warning

#### FAI-429C457EB60CBEA7 · redundant_requirement_response

- Subject：TR-001
- Chapter：WC-005
- Section：WC-005-11
- Requirement：TR-001
- Source FSF：FSF-10A2FB9CA14C788A
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：保修与维修方案已提供完整实质性响应，但服务承诺章节又以独立约束性语言重复相同承诺，构成冗余响应。
- 判定依据：主位置WC-005-07已完整覆盖免费保修与终身维修条款，包括细节。WC-005-11作为服务承诺章节，以编号列表形式重述了相同的保修年限、费用承担和终身维修等核心承诺，形成另一份独立、可审查的响应，属于redundant_response。
- 建议处理：建议将服务承诺中的保修条目改为对保修与维修方案的明确引用，避免重复承诺。

#### FAI-462CE7223F77AB80 · confirmation_alias_fragmentation

- Subject：offered_product_brand
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-C312E4508B0F4B6F
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：Placeholder aliases PH-FIELD-OFFERED-PRODUCT-BRAND and PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED reference the same offered product brand fact but appear in different locations, creating unnecessary alias dispersion.
- 判定依据：Both placeholders denote the same product brand for the hyperconverged system. The fulltext uses the longer variant in hyperconverged-specific sections and the shorter variant in the technical response table, but they represent a single fact. This is a same_fact alias situation that should be consolidated.
- 建议处理：Replace one alias with the other throughout the document to maintain a single placeholder for the offered product brand fact.

#### FAI-5E199F64902608DC · redundant_requirement_response

- Subject：TR-002
- Chapter：WC-005
- Section：WC-005-05, WC-005-11
- Requirement：TR-002
- Source FSF：FSF-457AEC119D8C10D4
- 原文片段：[WC-005-05 服务响应机制] 我方郑重承诺，自最终验收合格之日起的整个保修期内，严格向采购人提供如下服务响应：- 7×24小时技术响应：提供多通道服务请求入口，服务请求及时受理并响应。- 24小时内维修工程师到达维修现场：自请求受理起计时，位于市内及周边指定区域的认证工程师携带常用备件及工具包，确保24小时内抵达医院机房现场，如遇特殊气象或交通管制，将通过高速铁路和预备车辆等措施保证时限。- 免费更换零配件：保修期内因自然故障导致的硬件、模块及功能性零部件均免费更换，更换所用的全部部件均为原厂全新件或同等规格备件，更换过程遵循原厂更换作业指导书，并在更换后完成不少于2小时的系统稳定性测试。- 保修期自验收合格之日起计算，保修期内所有响应和维修承诺不因设备使用时长而缩减。以上响应指标已集成至我方ITSM系统，系统自动监控各环节时效，任何可能超时的事件将自动升级至项目服务经理及分管副总经理，确保在时限内闭环。
- 问题说明：“服务承诺”小节重复陈述了已在主响应中完整给出的7x24响应和24小时到场承诺，构成冗余。
- 判定依据：主位置WC‑005‑05已详尽承诺7×24小时技术响应和24小时内到达现场，并包含具体的实施细节。WC‑005‑11服务承诺以独立的约束性语言重述了完全相同的操作承诺，未提供必要的新信息。根据规则，即使采用编号总结形式，此类重复应判定为冗余响应。
- 建议处理：建议删除“服务承诺”中的冗余表述，或将其改为导航性引用（如“详见服务响应机制”），避免独立重复承诺。

#### FAI-60DCBF422E0D2C49 · confirmation_alias_fragmentation

- Subject：origin
- Chapter：WC-002, WC-007
- Section：WC-002-10, WC-007-01
- Requirement：-
- Source FSF：FSF-A0B11895A84EF997
- 原文片段：[WC-002-10 超融合系统详细技术方案] 本投标方案拟采用 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌超融合系统，型号为 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}}，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，产地 {{PH-FIELD-ORIGIN-HYPERCONVERGED}}。该超融合系统是基于标准 x86 服务器的软件定义数据中心平台，预集成了计算虚拟化、分布式存储和网络虚拟化内核，并通过统一管理界面提供一站式运维。以下对该系统核心技术组件进行描述，具体性能指标和技术参数将在确认后与《技术要求点对点应答表》一并提交。
- 问题说明：Placeholder aliases PH-FIELD-ORIGIN and PH-FIELD-ORIGIN-HYPERCONVERGED reference the same origin fact for the hyperconverged system.
- 判定依据：Both placeholders denote the country of origin for the same hyperconverged hardware. The fulltext uses the -HYPERCONVERGED suffix in technical descriptions and the short form in the response table and index, yet they represent a single factual value.
- 建议处理：Consolidate the two placeholders into a single placeholder ID for the origin fact.

#### FAI-66051E4F81FC5AED · confirmation_alias_fragmentation

- Subject：offered_product_model
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-2C6CC01263D8FCC7
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：Placeholder aliases PH-FIELD-OFFERED-PRODUCT-MODEL and PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED reference the same product model.
- 判定依据：The product model for the hyperconverged system is denoted by both a generic and a scoped placeholder. No evidence suggests different model values; they are aliases for the same fact.
- 建议处理：Replace the aliased placeholders with a single, consistent placeholder ID.

#### FAI-E40F6E35B75AEBF5 · confirmation_alias_fragmentation

- Subject：manufacturer
- Chapter：WC-002, WC-008
- Section：WC-002-07, WC-008-03
- Requirement：-
- Source FSF：FSF-273E290630108E6F
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：Placeholder aliases PH-FIELD-MANUFACTURER and PH-FIELD-MANUFACTURER-HYPERCONVERGED refer to the same manufacturer fact.
- 判定依据：The manufacture of the hyperconverged system is described with both a generic placeholder and a scope‑specific variant. No separate manufacturer is indicated; they are aliases for the same fact.
- 建议处理：Use a single placeholder ID for the manufacturer fact to avoid duplication.

## 3. 未决语义事项

- uncertain findings：1
- 以下事项尚未确认构成全文问题，需要人工审核；它们未进入 issues[]。

- FSF-9CC90F09B1CAC944 [requirement_location] 关联=TR-031：因应答表内容未在提供的摘录中呈现，无法确认是否已按要求列出具体数值；当前上下文不足，判定为不确定。

## 4. 待确认事实治理

- FCA 总数：32
- Placeholder occurrences：76
- Alias 关系：4
- FCA 全部保持 pending；pending 不等于 confirmed，关系表不执行合并。

## 5. Semantic Audit 统计

- issue verdict：7
- acceptable：28
- uncertain：1
- promoted：7
- accepted disposition：28
- human review disposition：1

## 6. Coverage / Candidate 摘要

- Requirement unique：31
- Requirement location candidates：14
- Score distribution candidates：0
- Numeric candidates：27
- Terminology candidates：5
- Near duplicate candidates：0

## 7. Stage ⑩ 上游状态

- production_status：complete_with_blockers
- composition_status：completed_with_blockers
- chapter_count_blocked：8（仅为上游背景）

Stage ⑩ blocker、pending FCA 与 placeholder 数量不参与 Stage ⑪ audit_result；release_ready 仍固定为 false。
