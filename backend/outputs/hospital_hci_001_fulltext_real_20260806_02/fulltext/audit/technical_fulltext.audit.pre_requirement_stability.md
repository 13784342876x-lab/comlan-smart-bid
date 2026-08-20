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

- FAI 总数：12
- fatal：0
- blocking：5
- warning：7
- info：0

### blocking

#### FAI-8A16A33D982E3BF8 · coverage_content_mismatch

- Subject：TR-003
- Chapter：WC-005
- Section：WC-005-11
- Requirement：TR-003
- Source FSF：FSF-A6B496F300D1758B
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：主要位置WC-005-04为空，而服务承诺节仅声明将依据要求提供，未给出具体信息，构成覆盖内容不匹配。
- 判定依据：规划的主要节'服务机构与人员'没有任何内容，而外部节WC-005-11仅表示'依据招标文件要求提供'，既未提供地址、联系方式等实质信息，也未指向其他完整响应，属于内容缺失。
- 建议处理：在WC-005-04或具体响应节中填写制造商服务机构的地址、联系方式和技术人员数量。

#### FAI-A36BA14248E407B5 · coverage_content_mismatch

- Subject：TR-025
- Chapter：WC-007
- Section：WC-007-01
- Requirement：TR-025
- Source FSF：FSF-72F2196065183221
- 原文片段：[WC-007-01 技术要求点对点应答表] 本节依据招标文件第三部分投标须知第18条（技术响应文件）及第四部分合同条款附件6-2的要求，逐条对招标文件第二部分技术要求进行评议，完整填写《技术要求点对点应答表》，说明本次投标货物和相关服务内容与采购人、采购代理机构相应要求的偏离情况。
- 问题说明：Planned table response (WC-007-01) fails to provide the required item-by-item technical commentary; external evidence index (WC-007-02) is not a substitute.
- 判定依据：TR-025 explicitly requires逐条评议 and filling the 点对点应答表. The provided excerpts from WC-007-01 only refer to the obligation to fill the table and the retention of the original format, without any actual评议 or filled content. WC-007-02 is a supporting evidence index. Therefore, the core response is missing.
- 建议处理：Provide the complete 逐条评议 and the filled 点对点应答表 as stipulated in TR-025.

#### FAI-A832B3D5F4F180C8 · coverage_content_mismatch

- Subject：TR-019
- Chapter：WC-007
- Section：WC-007-01
- Requirement：TR-019
- Source FSF：FSF-D2484D94D9C6BA30
- 原文片段：[WC-007-01 技术要求点对点应答表] 本节依据招标文件第三部分投标须知第18条（技术响应文件）及第四部分合同条款附件6-2的要求，逐条对招标文件第二部分技术要求进行评议，完整填写《技术要求点对点应答表》，说明本次投标货物和相关服务内容与采购人、采购代理机构相应要求的偏离情况。
- 问题说明：Planned primary location (WC-007-01) contains no substantive response to TR-019; the external evidence index (WC-007-02) does not compensate for the missing response.
- 判定依据：TR-019 is a regulatory requirement that 加注“★”号条款为实质性条款. The provided primary location content only explains the method of filling the table but does not address how the bid complies with the substantive-clause rule. No other location supplies this information. Thus the substantive response is absent.
- 建议处理：Include a response that explicitly confirms how the bid handles ★ 号条款不得负偏离, for example in the scope of the 点对点应答 or a dedicated compliance statement.

#### FAI-B65359EFEE352BCC · coverage_content_mismatch

- Subject：TR-026
- Chapter：WC-007
- Section：WC-007-01
- Requirement：TR-026
- Source FSF：FSF-2F0D4A5233D8B2F9
- 原文片段：[WC-007-01 技术要求点对点应答表] 本节依据招标文件第三部分投标须知第18条（技术响应文件）及第四部分合同条款附件6-2的要求，逐条对招标文件第二部分技术要求进行评议，完整填写《技术要求点对点应答表》，说明本次投标货物和相关服务内容与采购人、采购代理机构相应要求的偏离情况。
- 问题说明：Planned table-response location (WC-007-01) does not contain any specific numerical values or content for TR-026; outside evidence index (WC-007-02) is not a response.
- 判定依据：TR-026 mandates that the 点对点应答表 must list concrete values and not merely '符合' or '满足'. The excerpt from WC-007-01 describes only the process and format of the table, failing to demonstrate any concrete response content. The evidence index in WC-007-02 is unrelated to substituting for the missing substantive reply. Thus, a substantive response is absent where required.
- 建议处理：Include the actual filled 点对点应答表 with concrete technical values as required by TR-026.

#### FAI-C26899D87C0594C0 · coverage_content_mismatch

- Subject：TR-018
- Chapter：WC-002, WC-007
- Section：WC-002-10, WC-007-01
- Requirement：TR-018
- Source FSF：FSF-6200811EEEB5426E
- 原文片段：[WC-002-10 超融合系统详细技术方案] 分布式存储引擎是超融合系统的核心组件。它采用全分布式的元数据管理架构，无中心节点瓶颈，将每个节点上的 SSD 和 HDD 呈现为统一存储资源池。该引擎逻辑上分为自动分层存储层与数据服务层。自动分层存储层将性能敏感的 I/O 操作重定向到 SSD 缓存，并使用智能算法将冷数据渐进迁移到经济型 HDD 层，同时保持 IO 路径优化以获得与本地磁盘相当的低延迟。数据服务层的具体功能与数据保护机制将在产品确认后依据制造商技术文档确定。数据一致性通过严格的写入确认机制保障，确保在任意节点故障时数据不丢失。
- 问题说明：Primary narrative location (WC-002-10) and planned table location (WC-007-01) do not contain substantive parameter responses; the outside evidence index (WC-007-02) also lacks the missing content.
- 判定依据：WC-002-10 introduces the proposed brand and architecture but explicitly defers all concrete performance indicators and technical parameters to an unfilled table. WC-007-01 only states that it will follow the original format and does not present any actual table content or values. Therefore, no substantive primary or table response for the expansive parameter list in TR-018 is present. WC-007-02 is a material index that does not fill this gap. This constitutes coverage content mismatch.
- 建议处理：Provide the substantive parameter values for TR-018 either in the detailed technical solution section or by filling and including the complete 点对点应答表.

### warning

#### FAI-0B6259DA5A953920 · confirmation_alias_fragmentation

- Subject：manufacturer
- Chapter：WC-002, WC-007
- Section：WC-002-04, WC-007-01
- Requirement：-
- Source FSF：FSF-9D6E8D021EAAC555
- 原文片段：[WC-002-04 部署架构] 整体部署采用至少三节点的标配集群起点（具体节点数量将根据工作负载和可用性要求在实施规划阶段确认），所有超融合节点由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 提供。节点将安装于同一机架或相邻机架，通过超融合互联交换机形成万兆冗余网络平面。每个超融合节点配置冗余电源、风扇和网络接口卡，上联至两台采用堆叠或 MLAG 技术的高性能交换机，以实现网络链路的冗余与负载分担。在交换侧，超融合互联交换机将按照行业最佳实践配置跨机箱链路聚合（M-LAG 或堆叠）消除单点网络故障。同时，将划分独立的 VLAN 用于存储东西向流量、管理流量、虚拟机业务流量和 vMotion 等流量，确保不同类型流量的逻辑隔离和 QoS 保障。
- 问题说明：Placeholders PH-FIELD-MANUFACTURER and PH-FIELD-MANUFACTURER-HYPERCONVERGED are confirmed aliases for the same manufacturer fact within the hyperconverged scope.
- 判定依据：PH-FIELD-MANUFACTURER appears in the technical compliance table (LOC-94D86B7430BAF248) for the hyperconverged product, while PH-FIELD-MANUFACTURER-HYPERCONVERGED is used in the deployment architecture (LOC-703F9430739D1B61) explicitly stating the manufacturer provides all hyperconverged nodes. Both refer to the same entity.
- 建议处理：Verify that both placeholders are resolved to the identical manufacturer name in the final bid.

#### FAI-45662C861ED7A717 · redundant_requirement_response

- Subject：TR-008
- Chapter：WC-003
- Section：WC-003-03, WC-003-10
- Requirement：TR-008
- Source FSF：FSF-9653F870E3E65869
- 原文片段：[WC-003-03 项目设计实施周期] - **交货期**：货到时间为自签订合同之日起30日内（特殊情况以合同为准）。- **安装完成**：货到之日起10日内（特殊情况以合同为准），完成所有设备的安装、连接及基础配置工作。- **交货地点**：采购人指定地点。
- 问题说明：Primary response in WC-003-10 is adequate; WC-003-03 repeats the exact same delivery location commitment as a separate, independently reviewable statement.
- 判定依据：WC-003-10 explicitly commits to delivery at '采购人指定地点' within the delivery scope. WC-003-03, under a different section on implementation timeline, also states '交货地点：采购人指定地点' as a standalone item. This constitutes a duplicate of the same unambiguous promise without adding new information, meeting the definition of redundant_response.
- 建议处理：Retain the delivery location commitment in WC-003-10 and remove the redundant item from WC-003-03, or consolidate the location information in one place.

#### FAI-462CE7223F77AB80 · confirmation_alias_fragmentation

- Subject：offered_product_brand
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-C312E4508B0F4B6F
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：Placeholders PH-FIELD-OFFERED-PRODUCT-BRAND and PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED are confirmed aliases for the same offered product brand fact within the hyperconverged scope.
- 判定依据：The generic placeholder PH-FIELD-OFFERED-PRODUCT-BRAND appears in the technical compliance table (LOC-94D86B7430BAF248) specifically describing the hyperconverged system's basic product information, while the scoped placeholder PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED is used in reliability and architecture sections (e.g., LOC-884C6C3D9C48C762). Both contexts refer to the brand of the offered hyperconverged system, demonstrating semantic equivalence.
- 建议处理：Confirm consistent substitution of both alias placeholders with the actual brand during final document preparation.

#### FAI-46C88A1ED96F1806 · redundant_requirement_response

- Subject：TR-002
- Chapter：WC-005
- Section：WC-005-11
- Requirement：TR-002
- Source FSF：FSF-D7AE16700B257EC6
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：服务承诺节（WC-005-11）以总结形式重复了服务响应机制节（WC-005-05）中的核心承诺，构成冗余响应。
- 判定依据：WC-005-05已经提供并承诺了7×24小时响应和24小时到场等具体内容，WC-005-11第三条以简洁版本复述同一义务，属于独立且有约束力的重复陈述，符合冗余响应判定。
- 建议处理：删除WC-005-11中的重复承诺或将表述改为引用WC-005-05，避免双重承诺。

#### FAI-60DCBF422E0D2C49 · confirmation_alias_fragmentation

- Subject：origin
- Chapter：WC-002, WC-007
- Section：WC-002-10, WC-007-01
- Requirement：-
- Source FSF：FSF-A0B11895A84EF997
- 原文片段：[WC-002-10 超融合系统详细技术方案] 本投标方案拟采用 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌超融合系统，型号为 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}}，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，产地 {{PH-FIELD-ORIGIN-HYPERCONVERGED}}。该超融合系统是基于标准 x86 服务器的软件定义数据中心平台，预集成了计算虚拟化、分布式存储和网络虚拟化内核，并通过统一管理界面提供一站式运维。以下对该系统核心技术组件进行描述，具体性能指标和技术参数将在确认后与《技术要求点对点应答表》一并提交。
- 问题说明：Placeholders PH-FIELD-ORIGIN and PH-FIELD-ORIGIN-HYPERCONVERGED are confirmed aliases for the same origin fact within the hyperconverged scope.
- 判定依据：The generic placeholder PH-FIELD-ORIGIN is used in the technical compliance overview (LOC-94D86B7430BAF248) to describe the hyperconverged system's origin, while PH-FIELD-ORIGIN-HYPERCONVERGED is used in the detailed system description (LOC-96F960E75C18F21B). Both relate exclusively to the hyperconverged product, making them semantically identical.
- 建议处理：Ensure both placeholders are replaced with the identical origin value to avoid inconsistencies.

#### FAI-66051E4F81FC5AED · confirmation_alias_fragmentation

- Subject：offered_product_model
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-2C6CC01263D8FCC7
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：Placeholders PH-FIELD-OFFERED-PRODUCT-MODEL and PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED are confirmed aliases for the same offered product model fact within the hyperconverged scope.
- 判定依据：The generic placeholder PH-FIELD-OFFERED-PRODUCT-MODEL appears in the technical compliance section (LOC-94D86B7430BAF248) for the hyperconverged system model, while the scoped placeholder is used in reliability design (LOC-884C6C3D9C48C762). Both exclusively designate the model of the hyperconverged product.
- 建议处理：Align the resolution of both model placeholders to prevent discrepancies in the final document.

#### FAI-8DA425BA8398143F · redundant_requirement_response

- Subject：TR-001
- Chapter：WC-005
- Section：WC-005-07, WC-005-11
- Requirement：TR-001
- Source FSF：FSF-CD99D1FCC1D2F633
- 原文片段：[WC-005-07 保修与维修方案] 我方完全响应招标文件TR-001条款，提供投标产品免费保修及终身维修的完整保障。具体的免费保修年限将在商务标书中予以明确，届时将满足或优于招标要求的最低5年基准（具体年限见{{placeholder_warranty_period}}）。在保修期内，所有因设备自身质量问题导致的硬件故障、软件缺陷及性能异常，均由我方承担全部维修、备件及人工费用，采购人无需支付任何额外费用。维修期间我方优先采用“先换后修”策略，即现场工程师携带合格备件直接替换故障单元，将停机时间压缩至最短，换下的故障件进入制造商维修流程，修复后经全检合格方可作为备件归库。保修期届满后，我方继续向采购人提供终身维修服务，仅按届时公示的零部件成本价格及合理工时费收取费用，且终身保留对本次采购设备的软件版本更新和电话技术支持。对于机房搬迁、设备扩容、系统优化等非故障性技术服务，我方也可根据采购人需要提供专项服务方案，价格另行协商。
- 问题说明：Primary response in WC-005-07 is sufficient; the formal service commitment in WC-005-11 restates the same warranty and repair promise using independently binding language, creating a redundant response.
- 判定依据：WC-005-07 contains a full, substantive description of the 5‑year free warranty and lifetime repair commitment. WC-005-11, a formal service commitment section, repeats the identical obligation ('提供不低于招标规定的免费保修年限', '保修期外提供终身维修') in a numbered list intended to be contractually binding. This constitutes a redundant, independent restatement of the same operative promise without adding necessary new information. According to requirement-location rules, redundant service‑commitment summaries are classified as redundant_response.
- 建议处理：Retain the full response in WC-005-07 and either remove the redundant warranty/repeat point from WC-005-11 or convert it to a short navigational reference.

## 3. 未决语义事项

- uncertain findings：0
- 以下事项尚未确认构成全文问题，需要人工审核；它们未进入 issues[]。


## 4. 待确认事实治理

- FCA 总数：32
- Placeholder occurrences：76
- Alias 关系：4
- FCA 全部保持 pending；pending 不等于 confirmed，关系表不执行合并。

## 5. Semantic Audit 统计

- issue verdict：12
- acceptable：24
- uncertain：0
- promoted：12
- accepted disposition：24
- human review disposition：0

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
