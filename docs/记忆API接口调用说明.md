# 记忆写入与查询 API 接口调用说明

> 后端启动后，通过 HTTP 接口直接操作记忆的详细说明。
> 适合：单条消息调试、最小化验证、排查写入链路问题。
>
> 与 Python 工具（`write_one_session.py`、`verify_company.py` 等）调用的是同一套接口，本文档为底层 HTTP 形式。

---

## 1. 接口总览

| 接口 | 方法 | 路径 | 作用 |
|---|---|---|---|
| 健康检查 | GET | `/open/v1/health` | 确认服务存活 |
| 添加消息 | POST | `/open/v1/memory/sync/add-message` | 把一条对话消息写入待处理队列 |
| 触发生成 | POST | `/open/v1/memory/sync/commit` | 对已添加的消息做记忆抽取并落库 |
| 查询记忆 | POST | `/open/v1/memory/items/query` | 按用户/会话/语义查询记忆条目 |

> 所有 POST 接口请求头均为 `Content-Type: application/json`。
> 服务地址默认为 `http://127.0.0.1:8366`。

---

## 2. 通用约定

### 2.1 变量定义

以下命令都依赖这几个变量，先定义一次：

```bash
SERVER=http://127.0.0.1:8366
PREFIX=zh-memory-v1          # 本工程统一 id-prefix
COMPANY=C001                 # 公司编号（大写）
SESSION=S044                 # session 编号（注意大小写，hash 型 session 保持原样）
```

### 2.2 三个身份字段的规则

| 字段 | 命名规则 | 示例 |
|---|---|---|
| `userId` | `${PREFIX}-${COMPANY}` | `zh-memory-v1-C001` |
| `agentId` | `${PREFIX}-${COMPANY}-agent` | `zh-memory-v1-C001-agent` |
| `sourceClient` | `${PREFIX}-${COMPANY}-${SESSION}` | `zh-memory-v1-C001-S044` |

`sourceClient` 是**会话级标识**：同一个 session 的所有消息共用一个 sourceClient，commit 时按它聚合处理，查询时也按它过滤。

> **关键**：`PREFIX` 必须与写入时一致（本工程为 `zh-memory-v1`）。用错前缀会查询到空数据，误以为写入失败。

---

## 3. 健康检查

```bash
curl -s $SERVER/open/v1/health
```

**预期返回：**

```json
{"data":{"status":"UP","service":"memind-server"}}
```

**排查**：如果请求失败或返回非 UP，检查后端是否启动（`pkill -f "memind-server spring-boot:run"` 确认没有残留、然后按操作手册重启）。

---

## 4. 添加消息（add-message）

### 4.1 请求格式

```bash
curl -s -X POST $SERVER/open/v1/memory/sync/add-message \
  -H "Content-Type: application/json" \
  -d "{
    \"userId\": \"$PREFIX-$COMPANY\",
    \"agentId\": \"$PREFIX-$COMPANY-agent\",
    \"message\": {
      \"role\": \"USER\",
      \"content\": [
        {\"type\": \"text\", \"text\": \"[project=quick-6c4883][session=$SESSION] 核心抵押物是合肥经开区紫云路288号综合办公楼，土地面积4200㎡\"}
      ],
      \"timestamp\": \"2025-06-12T09:00:00Z\"
    },
    \"sourceClient\": \"$PREFIX-$COMPANY-$SESSION\"
  }"
```

### 4.2 字段详解

| 字段 | 必填 | 说明 |
|---|---|---|
| `userId` | 是 | 记忆归属用户，即公司 |
| `agentId` | 是 | 代理标识，固定加 `-agent` 后缀 |
| `message.role` | 是 | `USER` 或 `ASSISTANT`，区分说话方 |
| `message.content` | 是 | **数组格式**，每项为 `{"type":"text","text":"..."}`；当前只支持 text 类型 |
| `message.timestamp` | 否 | ISO-8601 时间（UTC），用于解析相对时间（如"昨天"）；不传则以当前时间处理 |
| `sourceClient` | 是 | 会话级标识，见 2.2 节 |

**正文文本约定**：脚本写入时会在正文前加 `[project=quick-6c4883][session=$SESSION]` 前缀，把项目和会话信息带进上下文，便于记忆条目带项目归属。手动调试建议同样加上。

### 4.3 响应说明

```json
{"data":{"triggered":false}}
```

- `triggered` 表示该消息是否立刻触发了处理；通常为 `false`，真正的抽取在 commit 时统一进行。
- 多条消息（含 ASSISTANT 回复）都建议先 add，最后再 commit。

---

## 5. 触发记忆生成（commit）

### 5.1 请求格式

```bash
curl -s -X POST $SERVER/open/v1/memory/sync/commit \
  -H "Content-Type: application/json" \
  -d "{
    \"userId\": \"$PREFIX-$COMPANY\",
    \"agentId\": \"$PREFIX-$COMPANY-agent\",
    \"sourceClient\": \"$PREFIX-$COMPANY-$SESSION\"
  }"
```

### 5.2 字段详解

| 字段 | 必填 | 说明 |
|---|---|---|
| `userId` | 是 | 同 2.2 |
| `agentId` | 是 | 同 2.2 |
| `sourceClient` | 是 | **聚合该会话所有已 add 的消息**，一起做抽取、生成记忆条目并落库 |

### 5.3 响应说明

```json
{"data":{"status":"SUCCESS","rawDataIds":["..."],"itemIds":["..."],"insightIds":[],"durationMillis":"165974"}}
```

| 字段 | 说明 |
|---|---|
| `status` | `SUCCESS` 表示处理完成 |
| `rawDataIds` | 本次生成的原始数据 ID |
| `itemIds` | **本次生成的记忆条目 ID，非空表示确实抽取出了记忆** |
| `insightIds` | 洞察条目 ID（可能为空） |
| `durationMillis` | 处理耗时（毫秒） |

### 5.4 重要注意事项

1. **耗时可能很长**：一次 commit 要经过"分段 → 抽取 → 自检 → 写库"，复杂会话可达 2~6 分钟。
2. **超时设置**：curl 默认会一直等待（无超时），可以放心；但建议加 `--max-time 900` 保险。Python 脚本里 commit 超时默认 900 秒。
3. **判断成功看 itemIds**：`status=SUCCESS` 但 `itemIds` 为空，说明本次没有抽取出可落库的条目，不等于失败，但也可能触发"假成功"问题（见操作手册常见问题表）。
4. **完成后必须 verify**：commit 返回成功 ≠ 数据一定可查。务必用下一节的查询接口确认条目真的存在。

---

## 6. 查询记忆（items/query）

### 6.1 查询某 session 写入的条目

```bash
curl -s -X POST $SERVER/open/v1/memory/items/query \
  -H "Content-Type: application/json" \
  -d "{
    \"userId\": \"$PREFIX-$COMPANY\",
    \"agentId\": \"$PREFIX-$COMPANY-agent\",
    \"sourceClients\": [\"$PREFIX-$COMPANY-$SESSION\"],
    \"limit\": 10
  }"
```

这是 commit 后 verify 的标准姿势：**只查这个 session 产生的条目**。

### 6.2 查询公司全部条目（不按 session 过滤）

```bash
curl -s -X POST $SERVER/open/v1/memory/items/query \
  -H "Content-Type: application/json" \
  -d "{
    \"userId\": \"$PREFIX-$COMPANY\",
    \"agentId\": \"$PREFIX-$COMPANY-agent\",
    \"limit\": 100
  }"
```

> 不带 `query` 和 `sourceClients` 就是全量列出；`limit` 默认可能较小，需要看全量时调大（如 10000）。

### 6.3 语义检索（模拟问答）

```bash
curl -s -X POST $SERVER/open/v1/memory/items/query \
  -H "Content-Type: application/json" \
  -d "{
    \"userId\": \"$PREFIX-$COMPANY\",
    \"agentId\": \"$PREFIX-$COMPANY-agent\",
    \"query\": \"拜访方式 更正 电话沟通 现场拜访\",
    \"limit\": 10
  }"
```

带 `query` 时走语义检索（embedding 相似度），返回按相关度排序的条目——**这就是问答评测时的检索路径**。

### 6.4 分页查询

```bash
curl -s -X POST $SERVER/open/v1/memory/items/query \
  -H "Content-Type: application/json" \
  -d "{
    \"userId\": \"$PREFIX-$COMPANY\",
    \"agentId\": \"$PREFIX-$COMPANY-agent\",
    \"limit\": 10,
    \"offset\": 10
  }"
```

### 6.5 字段详解

| 字段 | 必填 | 说明 |
|---|---|---|
| `userId` / `agentId` | 是 | 同 2.2 |
| `sourceClients` | 否 | 数组，只查指定会话；不传则查全部 |
| `query` | 否 | 语义检索文本；不传则按最新/列表返回 |
| `limit` | 否 | 返回条数上限 |
| `offset` | 否 | 分页偏移 |

### 6.6 响应结构

```json
{
  "data": {
    "items": [
      {
        "id": "350469066928033792",
        "text": "项目quick-6c4883的批复文件编号为JSDR20250822001，已确认完整归档",
        "scope": "USER",
        "category": "event",
        "type": "FACT",
        "rawDataId": "..."
      }
    ]
  }
}
```

重点看 `items` 数组和每条 `text`（记忆内容）、`category`（类别）、`type`（FACT/…）。

---

## 7. 完整组合流程示例

一条命令串完成「写消息 → 生成 → 验证」：

```bash
# 1) 添加一条用户消息
curl -s -X POST $SERVER/open/v1/memory/sync/add-message \
  -H "Content-Type: application/json" \
  -d "{\"userId\":\"$PREFIX-$COMPANY\",\"agentId\":\"$PREFIX-$COMPANY-agent\",\"message\":{\"role\":\"USER\",\"content\":[{\"type\":\"text\",\"text\":\"[project=quick-6c4883][session=$SESSION] 测试记忆内容：贷款利率为年化4.45%\"}],\"timestamp\":\"2025-06-12T09:00:00Z\"},\"sourceClient\":\"$PREFIX-$COMPANY-$SESSION\"}" | cat

# 2) 触发记忆生成（可能耗时几分钟）
curl -s --max-time 900 -X POST $SERVER/open/v1/memory/sync/commit \
  -H "Content-Type: application/json" \
  -d "{\"userId\":\"$PREFIX-$COMPANY\",\"agentId\":\"$PREFIX-$COMPANY-agent\",\"sourceClient\":\"$PREFIX-$COMPANY-$SESSION\"}" | cat

# 3) 验证该 session 是否真的生成了条目
curl -s -X POST $SERVER/open/v1/memory/items/query \
  -H "Content-Type: application/json" \
  -d "{\"userId\":\"$PREFIX-$COMPANY\",\"agentId\":\"$PREFIX-$COMPANY-agent\",\"sourceClients\":[\"$PREFIX-$COMPANY-$SESSION\"],\"limit\":5}" | cat
```

---

## 8. 注意事项速查

| 现象 | 原因与处理 |
|---|---|
| health 返回非 UP | 后端未启动或端口不对，重启后端 |
| commit 报 500 Server Error | 多为 LLM 临时抖动，重试该 session 即可 |
| commit `status=SUCCESS` 但 `itemIds=[]` | 可能假成功（额度/模型问题），用 items/query 验证；参考 `docs/memory-write-llm-issues-guide.md` |
| 查询返回空但日志有落库 | 检查 `PREFIX`/`sourceClient` 是否与写入时完全一致（大小写、公司编号） |
| verify 查询超时 | 服务端可能仍在处理，稍等几秒再查 |
| hash 型 session 名 | 保持数据文件中的原始大小写，如 `V104P143cce9b1163`，不要转大写 |
| 时间字段 | 统一用 ISO-8601 且带 Z（UTC），如 `2025-06-12T09:00:00Z` |
