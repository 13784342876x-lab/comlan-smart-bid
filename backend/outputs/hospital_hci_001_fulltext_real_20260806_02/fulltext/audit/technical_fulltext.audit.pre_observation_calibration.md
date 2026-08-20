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

- FAI 总数：11
- fatal：0
- blocking：3
- warning：8
- info：0

### blocking

#### FAI-8A16A33D982E3BF8 · coverage_content_mismatch

- Subject：TR-003
- Chapter：WC-005
- Section：WC-005-11
- Requirement：TR-003
- Source FSF：FSF-A6B496F300D1758B
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：The planned primary section (WC-005-04) is empty. The only mention in WC-005-11 is a generic promise that the information will be provided, not the actual details.
- 判定依据：TR-003 requires specific manufacturer service institution details (address, contact, technician count). WC-005-04 has no content. WC-005-11 includes a short statement that the information will be provided per bidding requirements, which is a generic promise and not a substantive response. Thus primary response is missing and the outside location is a generic promise.
- 建议处理：Provide the actual manufacturer service institution details in the intended primary section or wherever the response is placed.

#### FAI-8A7AF7F4ABF4BE70 · coverage_content_mismatch

- Subject：TR-014
- Chapter：WC-008
- Section：WC-008-01, WC-008-03
- Requirement：TR-014
- Source FSF：FSF-57B765CCE60C2C73
- 原文片段：[WC-008-01 产品技术资料索引] 本次投标拟投超融合节点产品的基本标识信息如下：产品品牌为{{PH-FIELD-OFFERED-PRODUCT-BRAND}}，产品型号为{{PH-FIELD-OFFERED-PRODUCT-MODEL}}，制造商为{{PH-FIELD-MANUFACTURER}}，产地为{{PH-FIELD-ORIGIN}}。上述产品信息将在正式投标文件中以制造商出具的原厂技术资料予以佐证，确保信息的真实性和法律效力。
- 问题说明：The planned primary section (WC-008-01) does not contain the required response for TR-014. The only actual content is in WC-008-03, which is an evidence index with placeholders.
- 判定依据：WC-008-01 addresses TR-012, not TR-014. WC-008-03 explicitly responds to TR-014 but only provides a placeholder and a description of the required certifications; it does not include the actual manufacturer certificates. Therefore primary response is missing and the outside location is a partial evidence reference.
- 建议处理：Ensure the manufacturer certificates are added or the index is updated; verify whether the primary location should be reassigned.

#### FAI-8BA26CAB9936FC99 · coverage_content_mismatch

- Subject：TR-013
- Chapter：WC-008
- Section：WC-008-01, WC-008-02
- Requirement：TR-013
- Source FSF：FSF-9FF13236867C05E1
- 原文片段：[WC-008-01 产品技术资料索引] 本次投标拟投超融合节点产品的基本标识信息如下：产品品牌为{{PH-FIELD-OFFERED-PRODUCT-BRAND}}，产品型号为{{PH-FIELD-OFFERED-PRODUCT-MODEL}}，制造商为{{PH-FIELD-MANUFACTURER}}，产地为{{PH-FIELD-ORIGIN}}。上述产品信息将在正式投标文件中以制造商出具的原厂技术资料予以佐证，确保信息的真实性和法律效力。
- 问题说明：Planned primary section WC-008-01 does not contain the required response for TR-013. The actual response is found in WC-008-02 which serves as an evidence index, not a complete independent response.
- 判定依据：WC-008-01 is dedicated to TR-012 and does not address manufacturer capability evidence. WC-008-02 explicitly indexes TR-013 evidence but contains only placeholders and a commitment to provide test reports; it does not present the actual evidence. Therefore primary response is missing and the outside location is a partial evidence reference.
- 建议处理：Verify whether the evidence will be included in the final document; consider if the primary location should be adjusted.

### warning

#### FAI-5E199F64902608DC · redundant_requirement_response

- Subject：TR-002
- Chapter：WC-005
- Section：WC-005-05, WC-005-11
- Requirement：TR-002
- Source FSF：FSF-457AEC119D8C10D4
- 原文片段：[WC-005-05 服务响应机制] 我方郑重承诺，自最终验收合格之日起的整个保修期内，严格向采购人提供如下服务响应：- 7×24小时技术响应：提供多通道服务请求入口，服务请求及时受理并响应。- 24小时内维修工程师到达维修现场：自请求受理起计时，位于市内及周边指定区域的认证工程师携带常用备件及工具包，确保24小时内抵达医院机房现场，如遇特殊气象或交通管制，将通过高速铁路和预备车辆等措施保证时限。- 免费更换零配件：保修期内因自然故障导致的硬件、模块及功能性零部件均免费更换，更换所用的全部部件均为原厂全新件或同等规格备件，更换过程遵循原厂更换作业指导书，并在更换后完成不少于2小时的系统稳定性测试。- 保修期自验收合格之日起计算，保修期内所有响应和维修承诺不因设备使用时长而缩减。以上响应指标已集成至我方ITSM系统，系统自动监控各环节时效，任何可能超时的事件将自动升级至项目服务经理及分管副总经理，确保在时限内闭环。
- 问题说明：The primary response in WC-005-05 is complete and adequate. The outside section WC-005-11 restates the same warranty commitments as an independent formal summary, which may be considered a redundant response.
- 判定依据：WC-005-05 contains a detailed commitment covering all aspects of the warranty (7x24, 24h arrival, free parts, warranty calculation). WC-005-11, although a summary, independently repeats the core warranty promises in binding language (points 3 and 2), which constitutes an independent response rather than a mere reference. This is flagged for potential redundancy review.
- 建议处理：Review the service commitment summary (WC-005-11) for potential redundancy with the primary warranty response.

#### FAI-788D5F2B89FEDB52 · confirmation_alias_fragmentation

- Subject：brand
- Chapter：WC-002
- Section：WC-002-10
- Requirement：-
- Source FSF：FSF-5709B2216B883790
- 原文片段：[WC-002-10 超融合系统详细技术方案] 本投标方案拟采用 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌超融合系统，型号为 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}}，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，产地 {{PH-FIELD-ORIGIN-HYPERCONVERGED}}。该超融合系统是基于标准 x86 服务器的软件定义数据中心平台，预集成了计算虚拟化、分布式存储和网络虚拟化内核，并通过统一管理界面提供一站式运维。以下对该系统核心技术组件进行描述，具体性能指标和技术参数将在确认后与《技术要求点对点应答表》一并提交。
- 问题说明：Placeholder pair refers to the same offered product brand but uses separate tokens (generic vs. hyperconverged scoped), causing a placeholder alias inconsistency.
- 判定依据：Both placeholders are used interchangeably in the technical fulltext to denote the brand of the hyperconverged system. The generic PH-FIELD-OFFERED-PRODUCT-BRAND appears in product information sections alongside the scoped -HYPERCONVERGED variant, and the candidate fact type is identical ('offered_product_brand'). The schema match confirms a strict subset relationship and a trusted fact type match. Leaving distinct placeholders creates risk of conflicting values during filling.
- 建议处理：Merge the two placeholders into a single token (preferably the scoped version PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED) to avoid conflicting values when the placeholder is filled. If the generic placeholder is intentionally distinct, document the difference.

#### FAI-8DA425BA8398143F · redundant_requirement_response

- Subject：TR-001
- Chapter：WC-005
- Section：WC-005-07, WC-005-11
- Requirement：TR-001
- Source FSF：FSF-CD99D1FCC1D2F633
- 原文片段：[WC-005-07 保修与维修方案] 我方完全响应招标文件TR-001条款，提供投标产品免费保修及终身维修的完整保障。具体的免费保修年限将在商务标书中予以明确，届时将满足或优于招标要求的最低5年基准（具体年限见{{placeholder_warranty_period}}）。在保修期内，所有因设备自身质量问题导致的硬件故障、软件缺陷及性能异常，均由我方承担全部维修、备件及人工费用，采购人无需支付任何额外费用。维修期间我方优先采用“先换后修”策略，即现场工程师携带合格备件直接替换故障单元，将停机时间压缩至最短，换下的故障件进入制造商维修流程，修复后经全检合格方可作为备件归库。保修期届满后，我方继续向采购人提供终身维修服务，仅按届时公示的零部件成本价格及合理工时费收取费用，且终身保留对本次采购设备的软件版本更新和电话技术支持。对于机房搬迁、设备扩容、系统优化等非故障性技术服务，我方也可根据采购人需要提供专项服务方案，价格另行协商。
- 问题说明：Primary response adequate; outside commitment section restates the warranty promise independently.
- 判定依据：WC-005-07 provides a detailed warranty and maintenance plan, including free warranty for at least 5 years and lifetime repair. WC-005-11 (服务承诺) contains a separate, independently worded commitment that repeats the same operative terms. It acts as an independent response rather than a mere reference.
- 建议处理：Independent warranty commitment found outside planned primary; verify if acceptable as a restatement or warrants relocation.

#### FAI-A5CD8459AE5046BF · confirmation_alias_fragmentation

- Subject：model
- Chapter：WC-002
- Section：WC-002-10
- Requirement：-
- Source FSF：FSF-9BB54F7A2C3F3A30
- 原文片段：[WC-002-10 超融合系统详细技术方案] 本投标方案拟采用 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌超融合系统，型号为 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}}，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，产地 {{PH-FIELD-ORIGIN-HYPERCONVERGED}}。该超融合系统是基于标准 x86 服务器的软件定义数据中心平台，预集成了计算虚拟化、分布式存储和网络虚拟化内核，并通过统一管理界面提供一站式运维。以下对该系统核心技术组件进行描述，具体性能指标和技术参数将在确认后与《技术要求点对点应答表》一并提交。
- 问题说明：Duplicate placeholder for the same hyperconverged model fact; alias requires resolution.
- 判定依据：The hyperconverged product model is referenced as PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED in technical sections (LOC-96F960E75C18F21B, LOC-CDFDBA898F7F6C58) and as the generic PH-FIELD-OFFERED-PRODUCT-MODEL in the combined attribute table and index (LOC-94D86B7430BAF248, LOC-8C34CFBAF98414C4). The candidate fact type is an exact match, and the scoped token is a strict subset, indicating a single fact that has been split into two tokens.
- 建议处理：Merge the two placeholders into one (e.g., PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED) to prevent divergent values.

#### FAI-B050CE2A5740FA1C · redundant_requirement_response

- Subject：TR-008
- Chapter：WC-003
- Section：WC-003-03, WC-003-06, WC-003-10
- Requirement：TR-008
- Source FSF：FSF-21304F5BADC00686
- 原文片段：[WC-003-03 项目设计实施周期] - **交货期**：货到时间为自签订合同之日起30日内（特殊情况以合同为准）。- **安装完成**：货到之日起10日内（特殊情况以合同为准），完成所有设备的安装、连接及基础配置工作。- **交货地点**：采购人指定地点。
- 问题说明：Primary response adequate; outside implementation plan section includes a direct statement of delivery location as an independent response.
- 判定依据：WC-003-10 lists delivery location explicitly as '采购人指定地点'. WC-003-03 repeats this delivery location statement as part of the implementation timeline, functioning as an independent response. WC-003-06 mentions the same phrase in a contextual installation context, not as a standalone answer.
- 建议处理：Independent delivery location statement found outside planned primary; verify if acceptable as a restatement or warrants relocation.

#### FAI-E1761FF4A8FB1893 · confirmation_alias_fragmentation

- Subject：manufacturer
- Chapter：WC-002
- Section：WC-002-04
- Requirement：-
- Source FSF：FSF-3BA95326987EF810
- 原文片段：[WC-002-04 部署架构] 整体部署采用至少三节点的标配集群起点（具体节点数量将根据工作负载和可用性要求在实施规划阶段确认），所有超融合节点由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 提供。节点将安装于同一机架或相邻机架，通过超融合互联交换机形成万兆冗余网络平面。每个超融合节点配置冗余电源、风扇和网络接口卡，上联至两台采用堆叠或 MLAG 技术的高性能交换机，以实现网络链路的冗余与负载分担。在交换侧，超融合互联交换机将按照行业最佳实践配置跨机箱链路聚合（M-LAG 或堆叠）消除单点网络故障。同时，将划分独立的 VLAN 用于存储东西向流量、管理流量、虚拟机业务流量和 vMotion 等流量，确保不同类型流量的逻辑隔离和 QoS 保障。
- 问题说明：The same manufacturer fact is represented by two different placeholder tokens, causing an alias inconsistency.
- 判定依据：Throughout the document, the manufacturer for the hyperconverged system is indicated both with PH-FIELD-MANUFACTURER-HYPERCONVERGED (e.g., LOC-703F9430739D1B61, LOC-884C6C3D9C48C762) and with the generic PH-FIELD-MANUFACTURER (LOC-94D86B7430BAF248, LOC-FEF7BBD12F38354D). The candidate fact type is 'manufacturer' and the scoped token is a strict subset of the generic one, confirming they denote the same attribute. Uncoordinated filling could result in contradictory entries.
- 建议处理：Standardise on a single placeholder (e.g., PH-FIELD-MANUFACTURER-HYPERCONVERGED) and update all occurrences. Document any intentional distinction if the generic token is meant for a different scope.

#### FAI-F1EE2CF97DC69033 · context_reset

- Subject：WC-001_to_WC-002
- Chapter：WC-001, WC-002
- Section：WC-001-09, WC-002-01
- Requirement：-
- Source FSF：FSF-8691D6C6FB8BEB19
- 原文片段：[WC-001-09 建设目标] 以上目标既是对招标意图的精准承接，也是我院交付方案的价值锚点。后续各章节将围绕这些目标，从技术方案、实施计划、运维服务等方面展开详细阐述，确保方案与目标紧密对应。
- 问题说明：WC-002第一章段重新介绍项目需求，未承接WC-001已完成的深入需求分析，构成上下文重置。
- 判定依据：WC-001的结尾段落(LOC-0FAA00BA63E18857)已声明后续章节将围绕需求分析目标展开，但WC-002的开头段(LOC-40FD76FDA5AAC3F1)却重新以“我方深刻理解……迫切需求”启动，仿佛前文的需求理解章节不存在，形成了上下文重置。
- 建议处理：在WC-002首段直接引用WC-001的需求总结，将过渡修改为‘基于前述需求理解，我院方案设计原则如下……’，避免重复介绍项目背景。

#### FAI-F6C6AA23184F7497 · confirmation_alias_fragmentation

- Subject：origin
- Chapter：WC-002
- Section：WC-002-10
- Requirement：-
- Source FSF：FSF-C3E9E9650152FC73
- 原文片段：[WC-002-10 超融合系统详细技术方案] 本投标方案拟采用 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌超融合系统，型号为 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}}，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，产地 {{PH-FIELD-ORIGIN-HYPERCONVERGED}}。该超融合系统是基于标准 x86 服务器的软件定义数据中心平台，预集成了计算虚拟化、分布式存储和网络虚拟化内核，并通过统一管理界面提供一站式运维。以下对该系统核心技术组件进行描述，具体性能指标和技术参数将在确认后与《技术要求点对点应答表》一并提交。
- 问题说明：Placeholder pair refers to the same origin fact for the hyperconverged product but uses distinct tokens, creating an alias conflict.
- 判定依据：The document uses PH-FIELD-ORIGIN-HYPERCONVERGED in the hyperconverged system description (LOC-96F960E75C18F21B) and PH-FIELD-ORIGIN without scope in the unified basic info block (LOC-94D86B7430BAF248), both referencing the product origin. The candidate fact type 'origin' matches and the tokens are a strict subset, indicating they represent the same fact. Unresolved aliases may lead to inconsistent data entry.
- 建议处理：Consolidate into a single placeholder (preferably PH-FIELD-ORIGIN-HYPERCONVERGED) to ensure a single source of truth for the origin value.

## 3. 未决语义事项

- uncertain findings：5
- 以下事项尚未确认构成全文问题，需要人工审核；它们未进入 issues[]。

- FSF-AFC2C857DA1FE9BD [requirement_location] 关联=TR-031：The planned table location may contain the required responses, but excerpts only show introduction, not the specific values. The outside section is an evidence index unrelated to the specific answer requirement.
- FSF-7CBFD436775B6F94 [requirement_location] 关联=TR-022：TR-022 is a scoring rubric whose response is distributed across many sections. The recorded primary location (WC-001-03) contains only one part; the complete response is built from the listed supporting sections. All outside locations act as supporting references or evidence indices, not misplaced independent responses.
- FSF-1D3CE8953B7CD90B [requirement_location] 关联=TR-026：Evidence insufficient to determine substantive response: only a statement of intent to fill the response table is visible, not the actual filled content.
- FSF-E20491EB53AE3C19 [requirement_location] 关联=TR-025：Evidence insufficient to determine substantive response: only a statement of intent to fill the response table is visible, not the actual filled content.
- FSF-1F3D4A4E0C87072D [requirement_location] 关联=TR-019：Evidence insufficient to determine substantive response: only a statement of intent to fill the response table is visible, not the actual content addressing starred items.

## 4. 待确认事实治理

- FCA 总数：32
- Placeholder occurrences：76
- Alias 关系：4
- FCA 全部保持 pending；pending 不等于 confirmed，关系表不执行合并。

## 5. Semantic Audit 统计

- issue verdict：11
- acceptable：20
- uncertain：5
- promoted：11
- accepted disposition：20
- human review disposition：5

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
