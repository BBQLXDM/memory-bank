# Memind 知识图谱存储结构详解

> 本文档专门说明 Memind 知识图谱的**存储结构**：图数据落在哪些表、每张表的字段含义、写入与读取的数据流。
> 内容基于运行时数据库 `memind-server/data/memind-server.db` 的实测结构与数据。
>
> 完整图谱原理（建图/检索/配置）见 `docs/knowledge-graph-guide.md`。

---

## 目录

1. [总体框架：二维模型](#一总体框架归属维度的二维模型)
2. [7 张表的分工](#二7-张表的分工与关系)
3. [实体主表 memory_graph_entity](#1-memory_graph_entity--实体主表节点)
4. [实体提及表 memory_item_entity_mention](#2-memory_item_entity_mention--实体提及表)
5. [链接表 memory_item_link](#3-memory_item_link--item-链接表边)
6. [共现表 memory_entity_cooccurrence](#4-memory_entity_cooccurrence--实体共现表)
7. [别名字段相关表](#5-memory_graph_entity_alias--实体别名表)
8. [写入批次管理表](#6-memory_item_graph_batch--图写入批次表)
9. [数据流](#三数据流)
10. [一句话总结](#四一句话总结)

---

## 一、总体框架：归属维度的二维模型

图谱按 **「归属维度 + 图数据」** 组织。所有图相关表都带四个归属字段，把不同公司 / 用户 / 记忆空间的数据隔离：

| 字段 | 含义 | 示例 |
|---|---|---|
| `user_id` | 归属用户 | `benchmark-v107-C015` |
| `agent_id` | 归属智能体 | `benchmark-v107-C015-agent` |
| `memory_id` | 记忆空间 ID | 记忆空间标识 |

> **关键设计**：所有图数据都按这四层维度隔离，互不串扰。
> 这也是从存储层面防范跨公司 / 跨用户记忆干扰的基础。

---

## 二、7 张表的分工与关系

```
 实体相关                       链接相关                     写入管理
─────────────────────      ─────────────────────      ─────────────────
memory_graph_entity        memory_item_link           memory_item_graph_batch
memory_item_entity_mention memory_entity_cooccurrence memory_graph_alias_batch_receipt
memory_graph_entity_alias
```

- **实体相关**：存"节点"（实体）+ item 与实体的关联（提及）+ 别名
- **链接相关**：存"边"（item 间三种关系）+ 实体共现辅助
- **写入管理**：图批量写入的幂等与失败追踪

---

## 三、各表字段明细

### 1. `memory_graph_entity` —— 实体主表（节点）

当前 **760 行**，存图里的**节点**（实体本身）。

| 字段 | 含义 |
|---|---|
| `id` | 自增主键 |
| `user_id` / `agent_id` / `memory_id` | 归属维度 |
| `entity_key` | **实体唯一键**（归一化后，如 `organization:中国农业银行西安航空基地支行`） |
| `display_name` | 展示名 |
| `entity_type` | 实体类型 |
| `metadata` | 元数据（JSON） |
| `created_at` / `updated_at` / `deleted` | 时间戳 / 软删标记 |

**要点**

- `entity_key` 带类型前缀（`concept:` / `organization:` / `object:` …），是归一化后的主键；同名实体靠它去重合并。
- 实体类型实测分布：

| 类型 | 数量 | 说明 |
|---|---|---|
| CONCEPT | 280 | 概念 |
| OBJECT | 206 | 对象 |
| ORGANIZATION | 161 | 组织（公司等） |
| PERSON | 77 | 人物 |
| PLACE | 30 | 地点 |
| SPECIAL | 6 | 特殊实体 |

---

### 2. `memory_item_entity_mention` —— 实体提及表

当前 **1395 行**，记录**哪个 item 里提到了哪个实体**（item ↔ entity 的关联）。

| 字段 | 含义 |
|---|---|
| `id` / `user_id` / `agent_id` / `memory_id` | 主键 + 归属 |
| `item_id` | 哪个记忆 item |
| `entity_key` | 提到的哪个实体 |
| `confidence` | 提及置信度 |
| `metadata` / `created_at` / `updated_at` / `deleted` | 附加信息 / 时间戳 / 软删 |

**作用**：这是 **item 通过共同实体连接**的基础。检索时：拿到 item → 查它的实体 → 再找同实体的其他 item（即"实体邻居"）。

---

### 3. `memory_item_link` —— item 链接表（边）

当前 **4419 行**，存**边**（item 与 item 之间的关联），是图谱最核心的表。

| 字段 | 含义 |
|---|---|
| `id` / `user_id` / `agent_id` / `memory_id` | 主键 + 归属 |
| `source_item_id` / `target_item_id` | 链接两端 item |
| `link_type` | **TEMPORAL / SEMANTIC / CAUSAL** |
| `strength` | 强度 0~1 |
| `relation_code` | 关系码（temporal 的 before/overlap、causal 的 caused_by 等） |
| `evidence_source` | 证据来源（如 `vector_search`） |
| `metadata` / 时间戳 / `deleted` | 附加信息 / 软删 |

**实测样例**

```
SEMANTIC    strength=0.923  evidence=vector_search   ← 向量相似度高连边
TEMPORAL    relation=overlap strength=1.0            ← 时间窗重叠
TEMPORAL    relation=before  strength=0.6            ← 时间先后
```

**类型分布**

| link_type | 数量 | 占比 |
|---|---|---|
| TEMPORAL | 3218 | 73% |
| SEMANTIC | 1196 | 27% |
| CAUSAL | 5 | <1% |

> 这张表体现图谱本质：**节点是 item，边是三种关系**（时间关系最多）。因因果提示较稀缺，因果链接最少。

---

### 4. `memory_entity_cooccurrence` —— 实体共现表

当前 **541 行**，记录**两个实体在同一范围内共现的次数**。

| 字段 | 含义 |
|---|---|
| `id` / 归属字段 | 主键 + 归属 |
| `left_entity_key` / `right_entity_key` | 实体对 |
| `cooccurrence_count` | 共现计数 |
| `metadata` / 时间戳 / `deleted` | 附加信息 |

**作用**：辅助实体间关联强度统计，用于实体邻居扩展时的排序参考。

---

### 5. `memory_graph_entity_alias` —— 实体别名表

当前 **0 行**（未触发），存实体的别名映射。

| 字段 | 含义 |
|---|---|
| `id` / 归属字段 | 主键 + 归属 |
| `entity_key` | 正式实体 |
| `entity_type` | 实体类型 |
| `normalized_alias` | 归一化别名 |
| `evidence_count` | 别名证据数 |
| `metadata` / 时间戳 / `deleted` | 附加信息 |

**作用**：把"中国工商银行"和"工行"等写法归一到一个实体。当前 `aliasEvidenceMode=METADATA`（默认），别名主要靠元数据证据生成，本数据集中未产生别名记录。

---

### 6. `memory_item_graph_batch` —— 图写入批次表

当前 **785 行**，管理**异步图写入的批次状态**（写入管理表，非图数据本身）。

| 字段 | 含义 |
|---|---|
| `id` / 归属字段 | 主键 + 归属 |
| `extraction_batch_id` | 关联的抽取批次 |
| `state` | 批次状态 |
| `error_message` / `retry_promotion_supported` | 错误信息 / 是否支持重试 |
| 时间戳 | 记录时间 |

**作用**：图写入是批处理（一次 commit 可能产生一批边），此表保证**幂等与失败追踪**。

---

### 7. `memory_graph_alias_batch_receipt` —— 别名批回执表

当前 **0 行**，别名批量生成的回执表（与别名表配套，别名未触发因此为空）。

---

## 三、数据流

### 写入视角（一次 commit）

```
LLM 抽取产生 item + 实体提及
  → 实体归一化 / 消歧 → 插入或更新 memory_graph_entity
  → 插入 memory_item_entity_mention
  → 语义 / 时间 / 因果链接计算 → 批量插入 memory_item_link
  → 共现统计 → memory_entity_cooccurrence
  → 批次状态记入 memory_item_graph_batch
```

### 读取视角（一次检索）

```
提问 → 向量 / 关键词召回得 seed items
  → GraphOperations 查 entity / mention / link 表
  → 沿实体邻居、时间邻居、因果邻居扩展（GraphExpansionEngine）
  → 融合打分 → 返回 Top-K
```

---

## 四、一句话总结

> **图存储 = SQLite 里 7 张带归属维度的表**：实体表存节点、提及表存 item-实体关联、链接表存三种边（时间/语义/因果，带强度与关系码）、共现表和别名表做辅助、两张 batch 表管写入。**节点是记忆 item 和实体，边是经过 LLM / 向量 / 时间窗计算出的关系**，全部随 `memind-server.db` 持久化。