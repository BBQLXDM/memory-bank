# Memind 知识图谱说明

> 本文档详细说明 Memind 记忆系统中知识图谱（Knowledge Graph）的实现：**怎么建图、存哪里、字段什么样、检索时怎么用**。
> 内容基于实际代码与运行时数据库实测数据，方便快速理解原理与排查。
>
> 核心代码：`memind-core/src/main/java/com/openmemind/ai/memory/core/` 下
> `extraction/item/graph/`（建图）、`store/graph/`（存储）、`retrieval/graph/`（检索）

---

## 目录

1. [整体架构：三张图讲清楚](#一整体架构)
2. [建图：记忆是怎么变成图的](#二建图记忆是怎么变成图的)
3. [存储：图存在哪、长什么样](#三存储图存在哪长什么样)
4. [检索：图怎么帮回答更准](#四检索图怎么帮回答更准)
5. [配置项速查](#五配置项速查)
6. [FAQ](#六faq)

---

## 一、整体架构

### 一句话概括

```
LLM 抽取实体与因果
  → 向量余弦做「语义链接」 + 时间窗做「时间链接」 + LLM 提示做「因果链接」
  → 写入 SQLite 图相关表（持久化）
  → 检索时沿实体/时间/因果邻居扩展候选，增强召回
```

### 流程图

```
  ┌──────────────────────── 阶段1：建图（记忆写入/抽取时） ────────────────────────┐
  │                                                                              │
  │   LLM 抽取记忆 item（文本/结构化事实）                                          │
  │        │                                                                     │
  │        ├─▶ 实体抽取：LLM 产出实体提及 (mention)                                │
  │        │       └─▶ 实体归一化（名称/类型/噪声/主键）                            │
  │        │       └─▶ 实体消歧（同名合并，阈值 0.85）                              │
  │        │                                                                     │
  │        ├─▶ 语义链接：embedding 余弦相似度（阈值 0.82）                          │
  │        ├─▶ 时间链接：时间窗区间重叠分类（before/after/overlap）                 │
  │        └─▶ 因果链接：LLM 因果提示（caused_by / enabled_by / motivated_by）     │
  │                                                                              │
  └──────────────────────────────┬────────────────────────────────────────────────┘
                                 ▼
  ┌──────────────────────── 阶段2：存储（SQLite 持久化） ──────────────────────────┐
  │                                                                              │
  │   memory_graph_entity            实体主表                                       │
  │   memory_item_entity_mention     实体提及表                                     │
  │   memory_item_link               item 链接表（语义/时间/因果）                   │
  │   memory_entity_cooccurrence     实体共现表                                     │
  │   memory_graph_entity_alias      实体别名表                                     │
  │   memory_item_graph_batch / memory_graph_alias_batch_receipt  批记录/回执       │
  │                                                                              │
  └──────────────────────────────┬────────────────────────────────────────────────┘
                                 ▼
  ┌──────────────────────── 阶段3：检索（回答问题时） ─────────────────────────────┐
  │                                                                              │
  │   查询 → 向量/关键词召回 seed items                                            │
  │        → 图扩展引擎沿 实体邻居 / 时间邻居 / 因果邻居 扩展候选                    │
  │        → 融合打分（图通道权重 0.35）→ 返回 Top-K                               │
  │                                                                              │
  └──────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、建图：记忆是怎么变成图的

### 2.1 实体抽取（谁产生实体）

当 LLM 抽取记忆 item 时，**会同时产出实体（entity）及其提及（mention）**。

- 实体可以是：人名、公司名、产品名、地名、概念、金额对象等
- 相关类：`extraction/item/graph/EntityAliasObservation.java`、
  `entity/normalize/NormalizedEntityMentionCandidate.java`

### 2.2 实体归一化（同名不同写法 → 统一）

同一实体可能被写成不同形式，归一化保证落库时 key 一致：

| 处理 | 干的事 | 类 |
|---|---|---|
| **名称归一化** | 大小写、全半角、语言差异统一 | `LanguageAwareEntityNameNormalizer` |
| **类型映射** | 给实体分类（人名/公司/产品…） | `EntityTypeMapper` / `LocalizedEntityTypeMapper` |
| **噪声过滤** | 去掉无意义实体（代词、语气词…） | `EntityNoiseFilter` |
| **键规范化** | 生成稳定实体主键 `entity_key` | `EntityKeyCanonicalizer` |

### 2.3 实体消歧（同名实体是否合并）

- 模式：`EntityResolutionMode.EXACT`（默认，精确匹配）+ 保守启发式策略
- **合并阈值 0.85**：相似度高于 0.85 才合并，避免把不同实体误并
- 别名：`UserAliasDictionary`（用户可配） + 自动别名索引

### 2.4 三种链接（item 与 item 之间怎么连）

| 链接 | 依据 | 具体方法 | 每 item 上限 |
|---|---|---|---|
| **语义链接** | 向量相似度 | embedding **余弦相似度**（阈值 0.82） | ≤ 5 |
| **时间链接** | 时间窗 | 区间重叠分类（before / after / overlap） | ≤ 10 |
| **因果链接** | LLM 因果提示 | `caused_by` / `enabled_by` / `motivated_by` | ≤ 2 |

> **实测分布**（当前库 4419 条链接）：时间链接 3218、语义链接 1196、因果链接 5。
> 可见**时间链接占比最高**（71%），是图谱的主要结构；因因果提示较稀缺，因果链接最少。

---

## 三、存储：图存在哪、长什么样

### 3.1 两种实现

| 实现 | 说明 | 持久化？ |
|---|---|---|
| `InMemoryGraphOperations`（core 内） | 纯内存 `ConcurrentHashMap` | ❌ |
| `SqliteGraphOperations` / `MysqlGraphOperations` / `PostgresqlGraphOperations`（JDBC 插件） | 写入关系库表 | ✅ **运行时默认** |

> **实际运行态**：Spring Boot 检测到 SQLite 数据源 → 自动装配 `SqliteGraphOperations`
> → 图随记忆一起写入 `memind-server/data/memind-server.db`。

### 3.2 图相关表（实测）

| 表名 | 当前行数 | 作用 |
|---|---|---|
| `memory_graph_entity` | 760 | 实体主表 |
| `memory_item_entity_mention` | 1395 | item 中的实体提及 |
| `memory_item_link` | 4419 | item 间链接（语义/时间/因果） |
| `memory_entity_cooccurrence` | 541 | 实体共现统计 |
| `memory_graph_entity_alias` | 0 | 实体别名（未触发） |
| `memory_item_graph_batch` | 784 | 图写入批记录 |
| `memory_graph_alias_batch_receipt` | 0 | 别名批回执 |

### 3.3 表字段明细

**`memory_graph_entity`（实体主表）**

| 字段 | 含义 |
|---|---|
| `id` | 自增主键 |
| `user_id` / `agent_id` | 归属用户 / 智能体 |
| `memory_id` | 所属记忆空间 |
| `entity_key` | 实体唯一键（归一化后） |
| `display_name` | 展示名 |
| `entity_type` | 类型（PERSON/ORGANIZATION/CONCEPT…） |
| `metadata` | 附加元数据（JSON） |
| `created_at` / `updated_at` / `deleted` | 时间戳 / 软删 |

**`memory_item_link`（链接表）**

| 字段 | 含义 |
|---|---|
| `id` | 自增主键 |
| `user_id` / `agent_id` / `memory_id` | 归属维度 |
| `source_item_id` / `target_item_id` | 链接两端 item |
| `link_type` | 链接类型（TEMPORAL / SEMANTIC / CAUSAL） |
| `strength` | 链接强度（0~1） |
| `relation_code` | 关系码（如 caused_by） |
| `evidence_source` | 证据来源 |
| `metadata` / 时间戳 / `deleted` | 附加信息 |

**实体类型实测分布**

| 类型 | 数量 | 说明 |
|---|---|---|
| CONCEPT | 280 | 概念 |
| OBJECT | 206 | 对象 |
| ORGANIZATION | 161 | 组织（公司等） |
| PERSON | 77 | 人物 |
| PLACE | 30 | 地点 |
| SPECIAL | 6 | 特殊实体 |

### 3.4 如何直接查看图数据

```bash
# 图总览（行数）
cd /home/zzx/py/Memind-Local-Dev
python3 -c "
import sqlite3
conn = sqlite3.connect('memind-server/data/memind-server.db')
for t in ['memory_graph_entity','memory_item_entity_mention','memory_item_link','memory_entity_cooccurrence']:
    print(t, conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
"

# 看某公司的链接分布
python3 -c "
import sqlite3
conn = sqlite3.connect('memind-server/data/memind-server.db')
for r in conn.execute(\"SELECT link_type, COUNT(*) FROM memory_item_link WHERE user_id LIKE 'benchmark-v107-C015%' GROUP BY link_type\").fetchall():
    print(r)
"
```

---

## 四、检索：图怎么帮回答更准

`retrieval/graph/` 下的检索图辅助：

### 4.1 流程

```
用户提问
  → ① 向量检索 + 关键词检索得 seed items（Top-K 初选）
  → ② 图扩展：沿 实体邻居 / 时间邻居 / 因果邻居 / 实体同属 向外扩
  → ③ 融合打分：图通道权重 0.35，保护直接命中 top-3
  → ④ 返回 Top-K 结果
```

### 4.2 关键参数（扩展规模）

| 参数 | SIMPLE 检索 | DEEP 检索 |
|---|---|---|
| 种子（seed）上限 | 6 | 8 |
| 扩展后候选上限 | 12 | 16 |
| 语义邻居/种子 | 2 | 2 |
| 时间邻居/种子 | 2 | 2 |
| 因果邻居/种子 | 2 | 2 |
| 实体同属 item/实体 | 3 | 4 |

### 4.3 模式

| 模式 | 行为 |
|---|---|
| `OFF` | 不使用图 |
| `ASSIST`（默认） | 沿图扩展候选，增强召回 |
| `CONTEXT` | 把图邻居作为上下文组织给 LLM |

> **效果**：即使向量/关键词召回不完美，图能沿关系把「间接相关」的记忆带进来，
> 特别擅长**跨 item 的关联记忆**场景（如同一公司的不同业务事实通过共同实体串联）。

---

## 五、配置项速查

### 建图配置（`ItemGraphOptions`）

| 配置 | 默认 | 含义 |
|---|---|---|
| `enabled` | `true` | 是否建图 |
| `maxEntitiesPerItem` | 8 | 每 item 最多实体数 |
| `maxTemporalLinksPerItem` | 10 | 每 item 最多时间链接 |
| `maxSemanticLinksPerItem` | 5 | 每 item 最多语义链接 |
| `maxCausalReferencesPerItem` | 2 | 每 item 最多因果链接 |
| `semanticMinScore` | 0.82 | 语义链接阈值 |
| `resolutionMergeThreshold` | 0.85 | 实体合并阈值 |
| `resolutionMode` | `EXACT` | 实体消歧模式 |

### 检索配置（`RetrievalGraphOptions`）

| 配置 | 默认 | 含义 |
|---|---|---|
| `mode` | `ASSIST` | 检索图辅助模式 |
| `graphChannelWeight` | 0.35 | 图通道在融合中的权重 |
| `protectDirectTopK` | 3 | 保护直接命中的前 N 条 |
| `maxExpandedItems` | 12/16 | 扩展后候选上限 |

---

## 六、FAQ

| 问题 | 回答 |
|---|---|
| **1. 图能像 Neo4j 那样查询吗？** | 不能直接查询。它是**检索增强的内部结构**，不是独立的图查询引擎（无 Cypher 等）。 |
| **2. 图数据会持久化吗？** | **会**。运行时默认 JDBC+SQLite，7 张图表随 `memind-server.db` 落盘，重启不丢失。 |
| **3. 图谱对检索提升有多大？** | 沿实体/时间/因果邻居扩展候选，弥补纯向量召回不足，尤其**跨 item 关联记忆**场景。 |
| **4. 需要额外安装中间件吗？** | 不需要。默认随 SQLite 持久化；如需 MySQL/PostgreSQL，JDBC 插件已支持，改数据源配置即可。 |
| **5. 图谱重建条件？** | 图随记忆抽取/写入自动更新；`InsightGraphAssist`（洞察图辅助）默认关闭。 |
| **6. 常见问题排查？** | 实体为 0 → 检查模型是否支持实体抽取；链接少 → 降低阈值或加长文本；重启丢失 → 确认用的是 JDBC 插件而非纯 core。 |