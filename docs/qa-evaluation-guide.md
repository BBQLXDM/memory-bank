# 记忆检索 + 对话回答 评测说明

> 本文档说明基准测试中"记忆检索"和"对话回答"两个维度的评测方案、
> 脚本用法与输出结构。评测采用**两阶段分离**：先生成结果，再打分。

- 服务地址：`http://127.0.0.1:8366`
- 评测对象：三家公司的记忆库（`C017` / `C018` / `C020`，`id-prefix=benchmark-v107`）
- 问答样例来源：benchmark 文件中各公司的 `qa_items` 字段（`question` / `answer`）

---

## 一、评测方案总览

```
第一阶段  generate_qa_answers.py       第二阶段  evaluate_qa_results.py
┌──────────────────────────────┐      ┌──────────────────────────────┐
│ 每条 qa_items:               │      │ 读取 results.json:           │
│  1. 调用 /memory/retrieve    │─────▶│  1. 检索评测 Hit@K + MRR     │
│     （保存完整检索响应）       │      │  2. 回答评测 LLM 打分 0/1/2  │
│  2. 组织检索上下文            │      │  3. 汇总分析 → summary       │
│  3. 调用 LLM 作答            │      │                              │
└──────────────────────────────┘      └──────────────────────────────┘
```

**为什么分两阶段**：第一阶段的原始数据（检索响应、上下文、LLM 回答）全部落盘保存。
第二阶段只读文件打分，评测规则调整时**无需重新跑检索和回答**，节省大量 LLM 调用。

---

## 二、评测规则

### 1. 记忆检索（Hit@K + MRR）

对每条问题，把 `expected_answer` 展开为多个**关键片段变体**（完整串、分隔符前缀、
含数字的片段，如 `2025年12月`、`2100万元`、`90.48%`），再在检索结果 `items`
的前 K 条文本中定位：

- **Hit@K**：任一关键片段出现在前 K 条中即命中。默认 **K=5**
- **MRR**（平均倒数排名）：命中位置的倒数；未命中记 0

> 注意：纯数字短片段（如 `01`）已被过滤，避免"长编号中的 01"这类误命中。

### 2. 回答评测（LLM 裁判 0/1/2 分）

用 LLM 裁判对 **LLM 生成的回答** 与标准答案比对打分：

- **2**：回答完全包含标准答案关键事实（数值、日期、名称等一致）
- **1**：回答部分包含关键事实
- **0**：回答不包含关键事实

使用的 `JUDGE_PROMPT`：

```text
你是一个金融数据评估专家。请判断"检索到的记忆"是否包含了"标准答案"中的关键事实信息。

标准答案: {ground_truth}

检索到的记忆: {retrieved}

只回答一个数字:
- 2: 检索结果完全包含了标准答案的关键事实（数值、日期、名称等核心信息一致）
- 1: 检索结果部分包含了标准答案的关键事实
- 0: 检索结果不包含标准答案的关键事实

只回答数字(0/1/2):
```

（其中 `retrieved` 填入生成回答的文本）

### 3. 最终分析指标

- 分公司 + 总体：检索命中率、MRR、评分分布（2/1/0 计数）、平均分、≥1 分比例
- 输出 `summary.json`（含明细）与 `summary.csv`

---

## 三、脚本说明

### 第一阶段：`dev-tools/python/generate_qa_answers.py`

| 项 | 说明 |
|---|---|
| 功能 | 检索 + 生成回答，产出 `results.json` |
| 输入 | benchmark 目录、公司列表、LLM 配置 |
| 输出 | `benchmark-results/qa-answers/<时间戳>/results.json` |

每条记录（record）包含：

```json
{
  "company_id": "C017",
  "company_name": "联科绿筑新型建材有限公司",
  "qa_id": "std_联科绿筑_t0002",
  "question": "批复额度是否发生过调减？",
  "expected_answer": "2025年12月调减至2100万元",
  "answer_type": "number",
  "capability": "information_extraction",
  "difficulty": "medium",
  "evidence": [{"track": "standard", "session_id": "S001", "turn_id": "D1:4"}],
  "retrieval": { "...": "完整检索响应" },
  "retrieval_http_status": 200,
  "retrieval_status": "OK",
  "item_count": 5,
  "context": "检索上下文（LLM 输入）",
  "answer": "LLM 生成的回答",
  "answer_error": null
}
```

### 第二阶段：`dev-tools/python/evaluate_qa_results.py`

| 项 | 说明 |
|---|---|
| 功能 | 读取 `results.json`，检索评测 + 回答打分 + 汇总 |
| 输入 | 第一阶段产出的 `results.json` 路径、LLM 配置 |
| 输出 | `benchmark-results/qa-evaluation/<时间戳>/summary.json`、`summary.csv` |

评测结果明细（evaluated record）：

```json
{
  "company_id": "C017",
  "qa_id": "std_联科绿筑_t0002",
  "question": "...",
  "expected_answer": "...",
  "answer": "...",
  "retrieval_hit": true,
  "retrieval_hit_rank": 1,
  "retrieval_mrr": 1.0,
  "judge_score": 2,
  "judge_error": null
}
```

---

## 四、运行步骤

### 前置条件

1. 记忆库已完整写入（全量写入完成，失败项已重试补齐）
2. 后端在 `http://127.0.0.1:8366` 运行
3. DashScope API key 可用（当前模型 `qwen3.7-flash`）

### 第一步：生成检索与回答结果

```bash
cd /home/zzx/py/Memind-Local-Dev

python3 dev-tools/python/generate_qa_answers.py \
  --server http://127.0.0.1:8366 \
  --benchmark-dir "/mnt/d/CCBwork/2026/8月/benchmark_v1_0_7/standard/quick" \
  --companies C017 C018 C020 \
  --id-prefix benchmark-v107 \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model qwen3.7-flash \
  --llm-api-key sk-xxx
```

可用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--server` | `http://127.0.0.1:8366` | 后端地址 |
| `--benchmark-dir` | `$BENCHMARK_DIR` | benchmark 目录 |
| `--companies` | `C017 C018 C020` | 公司列表 |
| `--id-prefix` | `benchmark-v107` | 记忆 userId 前缀 |
| `--strategy` | `SIMPLE` | 检索策略 |
| `--top-k` | `8` | 上下文使用的条目数 |
| `--limit-per-company` | `0` | 每公司题目数限制（0=全部） |
| `--llm-base-url` / `--llm-model` / `--llm-api-key` | — | 回答 LLM 配置 |
| `--no-llm` | — | 只检索不回答 |

### 第二步：评测

```bash
python3 dev-tools/python/evaluate_qa_results.py \
  benchmark-results/qa-answers/<时间戳>/results.json \
  --llm-base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --llm-model qwen3.7-flash \
  --llm-api-key sk-xxx \
  --top-k 5
```

可用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `results` | （必填） | 第一阶段 results.json 路径 |
| `--top-k` | `5` | 检索评测 K 值 |
| `--judge-timeout` | `60` | 裁判 LLM 超时（秒） |
| `--llm-*` | 环境变量 | 裁判 LLM 配置 |
| `--out-dir` | `benchmark-results/qa-evaluation` | 结果目录 |

---

## 五、输出解读

终端最后会打印汇总：

```text
评测汇总:
  题目总数: 32
  检索 Hit@5: 90.62%  (29/32)
  检索 MRR: 0.8375
  回答评分分布: 2分=20  1分=7  0分=3  评分失败=0
  回答平均分: 1.531  (≥1分比例: 84.38%)
```

各字段含义：

| 字段 | 含义 |
|---|---|
| 检索 Hit@K | 标准答案关键信息能被检索到的比例（K 默认 5） |
| 检索 MRR | 命中位置越靠前越高，1.0 = 全部第一名命中 |
| 评分分布 | LLM 裁判给出 2/1/0 分的题目数 |
| 回答平均分 | 0~2 之间，越高回答质量越好 |
| ≥1分比例 | 回答至少部分命中标准答案的比例 |

---

## 六、常见问题

| 问题 | 排查 |
|---|---|
| 检索 hit 率偏低 | 先确认记忆库已完整写入；再确认 `--id-prefix` 与写入时一致（`benchmark-v107`） |
| 评分全部失败 | 检查 `--llm-api-key` 是否设置、DashScope 是否可用 |
| 某题答案命中但评分 0 | 打开 `summary.csv` 看该题 `answer` 与 `expected_answer`，判断是 LLM 回答跑偏还是裁判误判 |
| 重新评测 | 直接改 `evaluate_qa_results.py` 后重跑第二阶段即可，无需重跑第一阶段 |
