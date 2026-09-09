# 五家公司隔离记忆问答测试题目表

## 一、测试范围

本轮针对 5 家公司分别使用独立的 `userId` 和 `agentId` 进行问答检索测试，避免不同公司的记忆相互干扰。

| 公司 ID | 公司名称 | QA 数量 | 主要能力 |
|---|---|---:|---|
| C019 | 鑫科精密零部件制造有限公司 | 14 | 信息抽取、时间推理、知识更新、拒答 |
| C020 | 锐科航空装备股份有限公司 | 10 | 信息抽取、时间推理、知识更新、跨公司拒答 |
| C018 | 鑫源精密机械制造有限公司 | 12 | 信息抽取、时间推理、跨时间点拒答 |
| C017 | 联科绿筑新型建材有限公司 | 10 | 信息抽取、时间推理、额度和押品信息、合规拒答 |
| C016 | 绿能新源装备有限公司 | 10 | 信息抽取、时间推理、事实不存在拒答、跨公司拒答 |
| **合计** | **5 家公司** | **56** | |

本测试脚本测试的是：

```text
问题 → 独立公司记忆空间 → Top-K 记忆检索 → 基础答案证据命中判断
```

当前脚本不会调用独立的答案生成模型，因此测试结果属于**检索证据测试**，不是最终自然语言答案准确率。

## 二、完整问答测试

完整测试脚本：

```text
dev-tools/full-qa-pipeline-five-companies.py
```

测试链路为：

```text
记忆检索 → Top-K 证据检查 → 大模型答案生成 → 标准答案比对
```

脚本默认使用每家公司独立的 `userId` 和 `agentId`，每家公司使用前 5 条检索记忆作为生成模型的证据上下文。结果保存到：

```text
benchmark-results/full-pipeline/C016-full-results.jsonl
benchmark-results/full-pipeline/C017-full-results.jsonl
benchmark-results/full-pipeline/C018-full-results.jsonl
benchmark-results/full-pipeline/C019-full-results.jsonl
benchmark-results/full-pipeline/C020-full-results.jsonl
benchmark-results/full-pipeline/summary.json
```

后端和模型环境启动后，执行：

```bash
python3 dev-tools/full-qa-pipeline-five-companies.py
```

只测试某家公司或少量题目：

```bash
python3 dev-tools/full-qa-pipeline-five-companies.py \\
  --companies "鑫科精密零部件制造有限公司" \\
  --max-qa 1
```

只验证检索和证据，不调用答案生成模型：

```bash
python3 dev-tools/full-qa-pipeline-five-companies.py --no-generate
```

答案生成需要以下环境变量：

```text
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_CHAT_MODEL
```

每道题会记录：

- 检索 HTTP 状态；
- Top-K 证据数量；
- 证据是否命中及命中排名；
- 答案生成 HTTP 状态；
- 模型返回的结构化答案；
- 生成答案是否与标准答案匹配；
- 传给模型的检索证据。

拒答题会要求模型在证据不足、公司不一致或时间不一致时返回“无法确定”，并单独记录生成结果。

## 三、题目明细

### C019：鑫科精密零部件制造有限公司

| QA ID | 问题 | 答案类型 | 测试能力 | 标准答案/期望结果 |
|---|---|---|---|---|
| `std_鑫科精密_t0035` | 资金往来是否全部通过工行账户结算？ | fact | information_extraction | 所有资金往来均通过工行账户结算 |
| `std_鑫科精密_t0056` | 流动资金贷款是否存在支用限制？ | fact | information_extraction | 无支用限制 |
| `std_鑫科精密_t0062` | 核心产品有哪些？ | description | information_extraction | 精密齿轮、轴承、冲压件、注塑件 |
| `std_鑫科精密_t0075` | 配套设施完善情况怎样？ | fact | information_extraction | 配套设施完善 |
| `std_鑫科精密_t0083` | 2025 年 9 月中标的精密齿轮供应订单的投标编号是什么？ | name | temporal_reasoning/during | `QC-ZZ-2025091001` |
| `std_鑫科精密_t0095` | 90 天以上逾期金额是多少？ | number | information_extraction | 84 万元 |
| `std_鑫科精密_t0111` | 2026 年 2 月的联合检查结果如何？ | number | temporal_reasoning/during | 各项指标均合格，无整改项，无违规经营行为 |
| `std_鑫科精密_t0150` | 贷后检查周期是如何安排的？ | description | information_extraction | 每季度 1 次现场、每月 1 次非现场、每半年 1 次全面检查 |
| `std_鑫科精密_t0268` | 抵押物复评贬值对授信额度有何影响？ | description | information_extraction | 抵押物复评贬值会影响授信额度 |
| `std_鑫科精密_t0275` | 生产车间精密加工区域是否允许现场拍照或录音？ | description | information_extraction | 禁止现场拍照、录音 |
| `std_鑫科精密_u0283` | 在本次更正之前，研发贷利率是多少？ | number | knowledge_update/before | LPR 下浮 30 个基点 |
| `std_鑫科精密_u0284` | 更正之后，研发贷的当前利率是多少？ | number | knowledge_update/latest | LPR 下浮 50 个基点 |
| `std_鑫科精密_a0288` | 鑫科精密零部件制造有限公司在 2024 年的授信额度是多少？ | abstention | abstention/timepoint_missing | 无法确定：未记录 2024 年授信额度 |
| `std_鑫科精密_a0289` | 鑫科精密零部件制造有限公司在 2023 年的授信额度是多少？ | abstention | abstention/timepoint_missing | 无法确定：未记录 2023 年授信额度 |

### C020：锐科航空装备股份有限公司

| QA ID | 问题 | 答案类型 | 测试能力 | 标准答案/期望结果 |
|---|---|---|---|---|
| `std_锐科航空_t0057` | 履约保函是否可以正常开立？ | fact | information_extraction | 履约保函可正常开立 |
| `std_锐科航空_t0108` | 2026 年 1 月的双随机检查结果如何？ | fact | temporal_reasoning/during | 双随机检查全部合格 |
| `std_锐科航空_t0151` | 最近一次现场检查的报告编号是多少？ | name | temporal_reasoning/latest | `DH-RK-2026031201` |
| `std_锐科航空_t0154` | 2025 年 12 月完成的全面排查报告编号是什么？ | name | temporal_reasoning/during | `FX-RK-20251201` |
| `std_锐科航空_t0172` | 担保能力是否符合准入要求？ | description | information_extraction | 担保能力充足，符合准入要求 |
| `std_锐科航空_t0190` | 续贷申请目前处于哪个流程阶段？ | fact | temporal_reasoning/latest | 已按批复推进合同续签 |
| `std_锐科航空_u0280` | 在本次更正之前，征信查询次数是多少？ | number | knowledge_update/before | 5 次 |
| `std_锐科航空_u0281` | 更正之后，当前征信查询次数是多少？ | number | knowledge_update/latest | 3 次 |
| `std_锐科航空_a0289` | 鑫源精密机械制造有限公司的剩余可用额度是多少？ | abstention | abstention/cross_company | 无法确定：当前记忆不属于鑫源精密 |
| `std_锐科航空_a0290` | 华泰重型装备制造有限公司的流动资金贷款额度是多少？ | abstention | abstention/cross_company | 无法确定：当前记忆不属于华泰重型装备 |

### C018：鑫源精密机械制造有限公司

| QA ID | 问题 | 答案类型 | 测试能力 | 标准答案/期望结果 |
|---|---|---|---|---|
| `std_鑫源精密_t0023` | 银票与订单融资额度如何支用？ | fact | information_extraction | 可随生产及订单循环支用 |
| `std_鑫源精密_t0033` | 除华夏银行外，是否还有其他银行的授信？ | fact | information_extraction | 无其他银行授信 |
| `std_鑫源精密_t0035` | 货款结算与回款是否全部通过华夏银行账户进行？ | fact | information_extraction | 全部货款结算与回款均在华夏银行账户 |
| `std_鑫源精密_t0105` | 企业是否存在失信被执行人记录？ | description | information_extraction | 企业、实际控制人、股东、核心管理人员均无相关记录 |
| `std_鑫源精密_t0111` | 园区企业信用评价等级是什么？ | fact | information_extraction | 园区企业信用评价 A 级 |
| `std_鑫源精密_t0112` | 双随机检查的结果如何？ | fact | information_extraction | 双随机检查全部合格 |
| `std_鑫源精密_t0155` | 最近一次贷后检查中，哪些方面被确认是正常的？ | description | temporal_reasoning/latest | 生产、订单、回款、押品均正常 |
| `std_鑫源精密_t0157` | 2025 年 12 月完成的风险排查报告编号是什么？ | name | temporal_reasoning/during | `FX-XY-202512-01` |
| `std_鑫源精密_t0229` | 对利率的敏感程度如何？ | fact | information_extraction | 客户对利率敏感 |
| `std_鑫源精密_t0240` | 提升哪类产品的占比？ | fact | information_extraction | 提升高附加值产品占比 |
| `std_鑫源精密_a0289` | 鑫源精密机械制造有限公司在 2024 年的授信额度是多少？ | abstention | abstention/timepoint_missing | 无法确定：未记录 2024 年授信额度 |
| `std_鑫源精密_a0290` | 鑫源精密机械制造有限公司在 2023 年的授信额度是多少？ | abstention | abstention/timepoint_missing | 无法确定：未记录 2023 年授信额度 |

### C017：联科绿筑新型建材有限公司

| QA ID | 问题 | 答案类型 | 测试能力 | 标准答案/期望结果 |
|---|---|---|---|---|
| `std_联科绿筑_t0002` | 批复额度是否发生过调减？ | number | information_extraction | 2025 年 12 月调减至 2100 万元 |
| `std_联科绿筑_t0019` | 截至 2026 年 3 月 8 日剩余可用额度是多少？ | number | temporal_reasoning/during | 剩余可用额度 350 万元 |
| `std_联科绿筑_t0058` | 应收账款保理业务目前状态如何？ | fact | temporal_reasoning/latest | 已暂停使用 |
| `std_联科绿筑_t0110` | 2025 年 10 月与淄博市粉煤灰加工厂的供货纠纷是如何结案的？ | number | temporal_reasoning/during | 2025 年 10 月 28 日结案，双方和解，无经济损失 |
| `std_联科绿筑_t0117` | 是否有环保处罚文书？ | fact | information_extraction | 无处罚文书 |
| `std_联科绿筑_t0147` | 评估机构是哪家公司？ | description | information_extraction | 滨州市恒信资产评估有限公司 |
| `std_联科绿筑_t0151` | 是否有到期未结清的贷款合同？ | date | information_extraction | 无到期未结清合同 |
| `std_联科绿筑_t0182` | 最近一次押品实地核查的结果如何？ | description | temporal_reasoning/latest | 厂房正常使用、设备运行良好、押品保管规范、账实相符 |
| `std_联科绿筑_t0205` | 历史放款资料审核通过率是多少？ | number | temporal_reasoning/range | 审核通过率 100% |
| `std_联科绿筑_t0212` | 催收记录是否完整？ | fact | information_extraction | 催收记录完整 |

### C016：绿能新源装备有限公司

| QA ID | 问题 | 答案类型 | 测试能力 | 标准答案/期望结果 |
|---|---|---|---|---|
| `std_绿能新源_t0032` | 是否存在网络贷款？ | fact | information_extraction | 无网络贷款 |
| `std_绿能新源_t0111` | 双随机检查是否全部合格？ | fact | information_extraction | 双随机检查全部合格 |
| `std_绿能新源_t0131` | 2026 年 1 月 5 日更新了哪些信息？ | description | temporal_reasoning/during | 年报、生产进度、回款计划、原材料采购合同 |
| `std_绿能新源_t0143` | 流动资金贷款循环额度到期日是什么时候？ | date | information_extraction | 2026 年 5 月 24 日 |
| `std_绿能新源_t0157` | 最近一次全面风险排查覆盖了哪些方面？ | description | temporal_reasoning/latest | 生产真实性、回款、现金流、环保、押品 |
| `std_绿能新源_t0193` | 会议明确了哪些关于续贷的安排？ | fact | information_extraction | 续贷安排 |
| `std_绿能新源_a0286` | 中路城建工程有限公司的担保额度是多少？ | abstention | abstention/cross_company | 无法确定：当前记忆不属于中路城建 |
| `std_绿能新源_a0287` | 华信精密机械有限公司的营收是多少？ | abstention | abstention/cross_company | 无法确定：当前记忆不属于华信精密 |
| `std_绿能新源_a0290` | 绿能新源装备有限公司是否有进口相关业务？如有，规模是多少？ | abstention | abstention/nonexistent_fact | 无法确定：当前记录没有进口业务信息 |
| `std_绿能新源_a0293` | 绿能新源装备有限公司的研发投入情况如何？ | abstention | abstention/entity_isolation | 无法确定：当前记忆未记录研发投入 |

## 三、后续统计指标

每家公司完成测试后，建议分别统计：

- Top-1 证据命中率
- Top-3 证据召回率
- Top-5 证据召回率
- 拒答题正确识别率
- 时间版本题命中率
- 编号和金额题命中率
- 跨公司干扰率
- HTTP 请求失败数

最终报告需要明确区分：

```text
检索证据召回率 ≠ 最终答案生成准确率
```

当前测试脚本只验证检索接口是否返回与标准答案相关的证据，并记录完整的 Top-K Items 和 Raw Data，供后续人工语义评估或答案生成测试使用。
