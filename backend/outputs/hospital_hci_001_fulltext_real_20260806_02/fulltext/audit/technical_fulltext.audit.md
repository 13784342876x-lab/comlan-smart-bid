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

- FAI 总数：10
- fatal：0
- blocking：2
- warning：8
- info：0

### blocking

#### FAI-24D2BE5EC4D01EC3 · coverage_content_mismatch

- Subject：TR-022
- Chapter：WC-001, WC-002, WC-003, WC-004, WC-006, WC-007, WC-008
- Section：S1, S3, S4, WC-001-02, WC-001-03, WC-001-04, WC-001-05, WC-001-06, WC-001-07, WC-001-08, WC-001-09, WC-002-04, WC-002-05, WC-003-01, WC-003-02, WC-003-04, WC-003-05, WC-003-06, WC-003-07, WC-003-08, WC-003-11, WC-004-01, WC-004-02, WC-004-03, WC-004-04, WC-004-05, WC-004-06, WC-004-07, WC-004-08, WC-004-09, WC-004-10, WC-007-01, WC-007-02, WC-008-03, WC-008-04, WC-008-05, WC-008-06, WC-008-07
- Requirement：TR-022
- Source FSF：FSF-8FE6842F489D87ED
- 原文片段：[WC-001-02 现状及建设基础] 本次建设的物理基础包括医院已有的数据中心机房环境，其供电、制冷、网络布线等均可满足新增超融合节点的部署条件。从软件层面看，医院已建立运维监控与灾备制度，为超融合系统整合到现有运维体系提供了管理基础。
- 问题说明：规划的主响应位置仅提供了一般性需求分析，未实质响应评分项所要求的材料或承诺；评分所需的实质性信息分散在多个专项章节中，主响应不充分。
- 判定依据：TR-022 是一个综合评分表，期望响应形式为mixed。计划的主位置 WC-001-03「应用环境」虽然声明了该需求，但内容仅为业务背景描述，并未提供任何证书、证明或直接针对评分项的应答。因此主响应为 inadequate。其他位置如「节能产品与环境标志产品认证响应」提供了具体承诺，「制造商资质材料索引」等提供了证据索引，这些共同构成了各部分的支持性回应，但没有任何单一位置能够独立满足整个评分要求。故 outside 位置大多为 supporting_reference 或 evidence_reference，独立完整响应均设为 no。primary_response_status 因此为 inadequate。
- 建议处理：需综合评审所有分部响应，以确认覆盖全部评分项且材料到位

#### FAI-8A16A33D982E3BF8 · coverage_content_mismatch

- Subject：TR-003
- Chapter：WC-005
- Section：WC-005-11
- Requirement：TR-003
- Source FSF：FSF-A6B496F300D1758B
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：计划主节「服务机构与人员」为空，未提供任何信息；另一位置仅做出笼统承诺，未给出地址、联系方式或技术人员数量等具体资料。
- 判定依据：TR-003 要求提供制造商服务机构地址、联系方式及技术人员数量。expected form 为 narrative_response。主计划位置 WC-005-04 内容为空，因此 primary_response_status 为 missing。outside 位置「服务承诺」第7条仅说明将依据招标文件提供，属于 generic_promise，未提供实质性信息，无法替代完整回答。故整体响应缺失。
- 建议处理：需补充制造商服务机构的具体地址、联系方式及技术人员数量

### warning

#### FAI-2298CBC136687471 · context_reset

- Subject：WC-005|WC-006
- Chapter：WC-005, WC-006
- Section：S1, WC-005-11
- Requirement：-
- Source FSF：FSF-3C4AA2156F4EDBC3
- 原文片段：[WC-005-11 服务承诺] 综上所述，我方向采购人做出以下明确的服务承诺，愿意将承诺内容作为合同附件受法律约束：1. 免费保修与终身维修：提供不低于招标规定的免费保修年限（具体年限见{{placeholder_warranty_period}}），保修期自最终验收合格之日开始计算保修期外提供终身维修，仅收取备件成本与合理人工费用。2. 保修期全免费：在保修期内因产品质量问题引发的所有维修、更换备件和人工费用均由我方承担，绝不向采购人收取任何费用。3. 7×24小时技术响应：24小时维修工程师到达现场，自保修期起至结束绝不降低响应标准。4. 备件保障：核心备件常备库存，确保24小时内替换，杜绝因备件等待造成的长时间业务中断。5. 巡检：根据采购人需要进行现场巡检并记录检查结果。6. 服务透明性：月度服务报告、季度评审会、实时事件工单进度，采购人可随时检核所有服务质量数据。7. 制造商服务机构：所投产品制造商服务机构情况（包括地址、联系方式及技术人员数量等）依据招标文件要求提供。以上承诺共同构成完整的服务承诺体系，如因我方原因未能履行，愿按合同约定承担违约责任与赔偿责任。
- 问题说明：从售后服务运行保障章节直接跳转到绿色供应链管理章节，缺少上下文衔接，后章重新引入独立主题，构成上下文重置。
- 判定依据：前章以明确的服务承诺段落结束，内容聚焦于免费保修、7×24响应、备件保障等售后服务事项，没有提及或引出绿色供应链的内容。后章的开头立即以政策法规和绿色供应链管理总体思路开始，完全重新设定主题和写作目标，与前章的售后服务上下文无关。两个章节之间没有任何过渡说明或逻辑关联，读者无法预期主题的切换，因此判定为上下文重置。
- 建议处理：建议在售后服务章节末尾增加过渡性提醒，或在后章开头简要说明章节安排原因，确保投标文件阅读的连贯性。

#### FAI-26A4CF894D81D23C · confirmation_alias_fragmentation

- Subject：manufacturer
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-5AB95CA3A05E1F69
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：存在两个均表示制造商的占位符，构成同事实别名。
- 判定依据：PH-FIELD-MANUFACTURER 与 PH-FIELD-MANUFACTURER-HYPERCONVERGED 指向同一制造商实体，应统一化处理。
- 建议处理：统一采用一个制造商占位符，消除别名歧义。

#### FAI-31996AA9C07C2BB0 · context_reset

- Subject：WC-006|WC-007
- Chapter：WC-006, WC-007
- Section：S4, WC-007-01
- Requirement：-
- Source FSF：FSF-63AF1F68C5ADB5F5
- 原文片段：[S4 四、绿色供应链管理先进性及拟提交证明材料] 以上所列文件将在投标文件中以原件扫描件形式正式提交，并由专人负责核对有效期与认证范围，确保与所投产品品牌、型号及制造商信息完全一致。若所投产品已经获得相关节能或环境标志认证，将直接按照评分规则计入相应分值；若相关证书尚在申请中，将顺延提交并在评审允许的时间窗口内补正。无论认证结果如何，针对供应商、材料、生产过程、物流及回收环节的绿色管理实践均独立构成方案优势，充分体现投标人在绿色供应链管理方面的真实能力与持续改进承诺。
- 问题说明：绿色供应链管理章节结束于证明材料提交说明，后章立即开始技术参数点对点应答表，无上下文衔接，构成上下文重置。
- 判定依据：前章结尾段落讨论绿色供应链管理先进性和拟提交证明材料的承诺，属于环保和认证内容。后章开头突然转入技术要求点对点应答，依据投标须知说明应答表填写方法，未对前章的绿色主题做任何回应或引出。两章主题无内在逻辑联系，且没有过渡语句，导致阅读连贯性断裂，判定为上下文重置。
- 建议处理：建议在绿色供应链管理章节末尾加入对下一章技术参数应答的简要引述，或在后章开头简要说明从供应链到技术参数的自然过渡，以提升文件整体连贯性。

#### FAI-462CE7223F77AB80 · confirmation_alias_fragmentation

- Subject：offered_product_brand
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-C312E4508B0F4B6F
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：文档中存在两个表示同一事实的产品品牌占位符，形成别名关系。
- 判定依据：PH-FIELD-OFFERED-PRODUCT-BRAND 与 PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED 均指向所投超融合系统品牌这一相同事实，视为同事实别名，应统一占位符避免混淆。
- 建议处理：将产品品牌占位符统一为单一标识（如全部使用 PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED 或明确映射规则），确保全文档引用一致。

#### FAI-5E199F64902608DC · redundant_requirement_response

- Subject：TR-002
- Chapter：WC-005
- Section：WC-005-05, WC-005-11
- Requirement：TR-002
- Source FSF：FSF-457AEC119D8C10D4
- 原文片段：[WC-005-05 服务响应机制] 我方郑重承诺，自最终验收合格之日起的整个保修期内，严格向采购人提供如下服务响应：- 7×24小时技术响应：提供多通道服务请求入口，服务请求及时受理并响应。- 24小时内维修工程师到达维修现场：自请求受理起计时，位于市内及周边指定区域的认证工程师携带常用备件及工具包，确保24小时内抵达医院机房现场，如遇特殊气象或交通管制，将通过高速铁路和预备车辆等措施保证时限。- 免费更换零配件：保修期内因自然故障导致的硬件、模块及功能性零部件均免费更换，更换所用的全部部件均为原厂全新件或同等规格备件，更换过程遵循原厂更换作业指导书，并在更换后完成不少于2小时的系统稳定性测试。- 保修期自验收合格之日起计算，保修期内所有响应和维修承诺不因设备使用时长而缩减。以上响应指标已集成至我方ITSM系统，系统自动监控各环节时效，任何可能超时的事件将自动升级至项目服务经理及分管副总经理，确保在时限内闭环。
- 问题说明：主响应节「服务响应机制」提供了完整、独立可审查的服务承诺，同时「服务承诺」节作为独立完整重复，形成冗余独立响应。
- 判定依据：主位置 WC-005-05 明确承诺了7x24响应、24小时到场、免费更换零配件及保修期起算点，内容具体、无占位符，满足 narrative_response 要求。WC-005-11「服务承诺」节以编号方式重述相同承诺，构成独立的完整回答，即使主响应消失，该节也可单独满足要求。因此 outside 位置被标记为 independent_response/substantive/yes，但本身无必要。
- 建议处理：无需操作

#### FAI-60DCBF422E0D2C49 · confirmation_alias_fragmentation

- Subject：origin
- Chapter：WC-002, WC-007
- Section：WC-002-10, WC-007-01
- Requirement：-
- Source FSF：FSF-A0B11895A84EF997
- 原文片段：[WC-002-10 超融合系统详细技术方案] 本投标方案拟采用 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌超融合系统，型号为 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}}，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，产地 {{PH-FIELD-ORIGIN-HYPERCONVERGED}}。该超融合系统是基于标准 x86 服务器的软件定义数据中心平台，预集成了计算虚拟化、分布式存储和网络虚拟化内核，并通过统一管理界面提供一站式运维。以下对该系统核心技术组件进行描述，具体性能指标和技术参数将在确认后与《技术要求点对点应答表》一并提交。
- 问题说明：文档中存在两个表示同一产地事实的占位符，形成别名。
- 判定依据：PH-FIELD-ORIGIN 与 PH-FIELD-ORIGIN-HYPERCONVERGED 均指向超融合系统产地这一相同事实，属于同事实别名，应予合并。
- 建议处理：将产地占位符统一，避免因别名导致未来赋值时出现不一致。

#### FAI-66051E4F81FC5AED · confirmation_alias_fragmentation

- Subject：offered_product_model
- Chapter：WC-002, WC-007
- Section：WC-002-07, WC-007-01
- Requirement：-
- Source FSF：FSF-2C6CC01263D8FCC7
- 原文片段：[WC-002-07 可靠性设计] 对于一个承载医院核心业务的信息系统，任何非计划停机都可能对患者生命健康产生间接影响。因此，本方案围绕 {{PH-FIELD-OFFERED-PRODUCT-BRAND-HYPERCONVERGED}} 品牌的 {{PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED}} 超融合系统，由 {{PH-FIELD-MANUFACTURER-HYPERCONVERGED}} 制造，设计了全方位的可靠性保障机制，覆盖硬件冗余、软件容错、数据保护和运维操作等多个层面。
- 问题说明：用于表示超融合系统型号的占位符存在两个别名。
- 判定依据：PH-FIELD-OFFERED-PRODUCT-MODEL 和 PH-FIELD-OFFERED-PRODUCT-MODEL-HYPERCONVERGED 均指向同一型号事实，属于同事实别名，应统一。
- 建议处理：将产品型号占位符合并为统一令牌，避免后续填充时出现差异。

#### FAI-8DA425BA8398143F · redundant_requirement_response

- Subject：TR-001
- Chapter：WC-005
- Section：WC-005-07, WC-005-11
- Requirement：TR-001
- Source FSF：FSF-CD99D1FCC1D2F633
- 原文片段：[WC-005-07 保修与维修方案] 我方完全响应招标文件TR-001条款，提供投标产品免费保修及终身维修的完整保障。具体的免费保修年限将在商务标书中予以明确，届时将满足或优于招标要求的最低5年基准（具体年限见{{placeholder_warranty_period}}）。在保修期内，所有因设备自身质量问题导致的硬件故障、软件缺陷及性能异常，均由我方承担全部维修、备件及人工费用，采购人无需支付任何额外费用。维修期间我方优先采用“先换后修”策略，即现场工程师携带合格备件直接替换故障单元，将停机时间压缩至最短，换下的故障件进入制造商维修流程，修复后经全检合格方可作为备件归库。保修期届满后，我方继续向采购人提供终身维修服务，仅按届时公示的零部件成本价格及合理工时费收取费用，且终身保留对本次采购设备的软件版本更新和电话技术支持。对于机房搬迁、设备扩容、系统优化等非故障性技术服务，我方也可根据采购人需要提供专项服务方案，价格另行协商。
- 问题说明：主响应位置（保修与维修方案）详细阐述了免费保修、终身维修、替换策略等内容，承诺年限满足或优于5年，构成完整响应。服务承诺章节以独立格式再次做出相同实质承诺，属于独立完整响应，存在冗余但无明显冲突。
- 判定依据：RLA-03580388C6622B56 明确响应条款，描述保修年限将满足/优于5年基准，并详细说明保修期内外服务。RLA-B03C78D7349A2B9D 作为专门的服务承诺段落，第1点独立陈述免费保修与终身维修，即使独立阅读也能完全满足要求，因此被视为独立额外响应。两个位置内容一致但表达方式不同。
- 建议处理：评估是否为冗余响应；若属于可接受的重申则无需调整，否则可考虑精简以避免重复。

## 3. 未决语义事项

- uncertain findings：5
- 以下事项尚未确认构成全文问题，需要人工审核；它们未进入 issues[]。

- FSF-AFC2C857DA1FE9BD [requirement_location] 关联=TR-031：计划表格位置声明应答表已填写，但表格内容仅以摘要形式呈现，无法判断是否包含具体数值；该需求要求必须列出具体数值或内容，故当前证据不足以确认答复充分。
- FSF-86376EC61EC56A5A [requirement_location] 关联=TR-018：要求以表格响应，但关键表格（技术要求点对点应答表）内容未在本次审查全文文本中呈现，仅提供占位说明。计划主方案位置提供概述但未填充具体参数。其他位置为外围配套或证据索引。整体无法确认核心表格响应是否足以满足要求。
- FSF-1D3CE8953B7CD90B [requirement_location] 关联=TR-026：要求应答表必须列出具体数值或内容。实际表格内容未展示，无法判断是否满足格式与实质要求。证据索引仅提供支撑映射。
- FSF-E20491EB53AE3C19 [requirement_location] 关联=TR-025：要求对招标技术规格逐条评议并填写应答表说明偏离。应答表存在但内容缺失，无法评估是否逐条评议。索引部分仅为证据支撑。
- FSF-1F3D4A4E0C87072D [requirement_location] 关联=TR-019：要求确保带★条款无负偏离。虽提供应答表占位，但无具体内容，无法确认是否各★项均已满足。证据索引不可替代实质响应。

## 4. 待确认事实治理

- FCA 总数：32
- Placeholder occurrences：76
- Alias 关系：4
- FCA 全部保持 pending；pending 不等于 confirmed，关系表不执行合并。

## 5. Semantic Audit 统计

- issue verdict：10
- acceptable：21
- uncertain：5
- promoted：10
- accepted disposition：21
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
