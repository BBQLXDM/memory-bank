# 记忆写入 API 格式说明

> 本文档记录 Memind 后端 `/open/v1/memory/*` 接口的请求/响应 JSON 格式，
> 防止手动写 JSON 时出错（尤其容易踩 `role` 大小写、`malformed_json` 的坑）。

- 服务地址：`http://127.0.0.1:8366`
- 所有接口：`POST` + `Content-Type: application/json`
- 基础路径：`/open/v1/memory`

---

## 通用注意点（最容易出错）

1. **`role` 必须是大写枚举**
   - 合法值：`"USER"`、`"ASSISTANT"`
   - 写小写 `"user"` / `"assistant"` 会返回 `400 malformed_json`（实际是反序列化失败，不是 JSON 语法错）
2. **`timestamp` 必须是 ISO-8601 格式**
   - 合法示例：`"2026-08-13T10:30:00Z"`
   - 不要写 `2026-08-13 10:30:00`
3. **`content` 必须是数组**，里面每个元素是 content block 对象：
   ```json
   {"type": "text", "text": "消息内容"}
   ```
4. **`userId`、`agentId`、`message` 是必填**；缺一个返回 `400 validation_failed`
5. 在 bash 里发中文 JSON 时，**强烈建议把 JSON 写入文件，用 `--data-binary @文件` 发送**，
   避免终端把中文/引号搞坏导致 `malformed_json` 或 `HTTP 000`。
   ```bash
   curl -X POST http://127.0.0.1:8366/open/v1/memory/sync/add-message \
     -H "Content-Type: application/json" \
     --data-binary @/tmp/add-message.json
   ```

---

## 1. 写入消息

`POST /open/v1/memory/sync/add-message`

### 请求体

```json
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "message": {
    "role": "USER",
    "content": [
      {
        "type": "text",
        "text": "我的名字是测试记忆体，喜欢的颜色是蓝色，住在北京。"
      }
    ],
    "timestamp": "2026-08-13T10:30:00Z"
  },
  "sourceClient": "memory-test-client"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `userId` | string | 是 | 用户标识 |
| `agentId` | string | 是 | Agent 标识 |
| `message.role` | string | 是 | **大写** `USER` / `ASSISTANT` |
| `message.content` | array | 是 | content block 数组 |
| `message.content[].type` | string | 是 | 目前用 `text` |
| `message.content[].text` | string | 是 | 消息正文 |
| `message.timestamp` | string | 否 | ISO-8601 时间 |
| `sourceClient` | string | 否 | 来源客户端标识 |

### 响应

```json
{"data":{"triggered":false}}
```

- `triggered: false` = 消息已入队，未触发抽取
- `triggered: true` = 触发抽取，会带 `result` 字段

---

## 2. 提交抽取（commit）

`POST /open/v1/memory/sync/commit`

### 请求体

```json
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "sourceClient": "memory-test-client"
}
```

### 响应

```json
{
  "data": {
    "status": "SUCCESS",
    "rawDataIds": ["56a8cf1e-851d-42e6-8522-6fac8dca6273"],
    "itemIds": ["346179698843324416"],
    "insightIds": [],
    "insightPending": false,
    "durationMillis": "31640"
  }
}
```

- `status: SUCCESS` = 提交成功，记忆已落库
- `itemIds` 是抽取出的记忆条目 ID
- `rawDataIds` 是原始数据 ID

---

## 3. 查询记忆条目

`POST /open/v1/memory/items/query`

### 请求体

```json
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "limit": 10,
  "sourceClients": ["memory-test-client"]
}
```

可选过滤字段：`scope`、`categories`、`sourceClients`、`rawDataTypes`、`timeRange`、`metadataFilter`、`limit`、`cursor`

### 响应

```json
{
  "data": {
    "items": [
      {
        "id": "346179698843324418",
        "text": "User's favorite color is blue.",
        "scope": "USER",
        "category": "profile",
        "type": "FACT",
        "rawDataId": "56a8cf1e-851d-42e6-8522-6fac8dca6273",
        "rawDataType": "CONVERSATION",
        "sourceClient": "memory-test-client",
        "observedAt": "2026-08-13T10:30:00Z",
        "createdAt": "2026-08-13T06:34:31.912285920Z",
        "metadata": {
          "vectorId": "48f8f9ac-d6ca-41ee-b3af-3ebf3cc52b0f",
          "sourceClient": "memory-test-client",
          "insightTypes": ["preferences"]
        }
      }
    ],
    "nextCursor": null
  }
}
```

> 用 `sourceClients` 过滤来判断“某个 session 是否已写入”，是推荐做法（见下节）。

---

## 4. 查询原始数据

`POST /open/v1/memory/raw-data/query`

### 请求体

```json
{
  "userId": "memory-test-001",
  "agentId": "memory-test-001-agent",
  "sourceClients": ["memory-test-client"],
  "include": {"segment": true, "metadata": true},
  "limit": 10
}
```

### 响应

```json
{
  "data": {
    "rawData": [
      {
        "id": "56a8cf1e-851d-42e6-8522-6fac8dca6273",
        "type": "CONVERSATION",
        "sourceClient": "memory-test-client",
        "caption": "...",
        "metadata": {},
        "segment": {},
        "startTime": "2026-08-13T10:30:00Z",
        "endTime": "2026-08-13T10:30:00Z",
        "createdAt": "2026-08-13T06:34:31.912285920Z"
      }
    ],
    "nextCursor": null
  }
}
```

---

## 5. 用 sourceClient 实现“已写入就跳过”

推荐做法：每个 session 用独立的 `sourceClient`，例如：

```
benchmark-v107-C017-S001
benchmark-v107-C017-S002
```

写入前先查一下：

```bash
curl -s -X POST http://127.0.0.1:8366/open/v1/memory/items/query \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "benchmark-v107-C017",
    "agentId": "benchmark-v107-C017-agent",
    "sourceClients": ["benchmark-v107-C017-S001"],
    "limit": 1
  }'
```

- `items` 非空 → 该 session 已写入，跳过
- `items` 为空 → 未写入，继续

> 这样可以跨程序复用：只要两个程序用同一套 `sourceClient` 命名规则，就能互相识别已写入的 session。

---

## 常见错误排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `400 malformed_json` | role 大小写错 / 终端破坏 JSON / 编码问题 | 用大写 `USER`；用 `--data-binary @文件` |
| `HTTP 000` | 终端粘贴命令被破坏 / 后端没启动 / 网络不通 | 用文件方式发；检查后端 `curl .../open/v1/health` |
| `400 validation_failed` | 缺 userId/agentId/message | 补全必填字段 |
| `500 internal_error` | 后端 commit 内部 LLM/向量失败 | 看后端日志，检查模型服务 |
| 查询为空但写入“成功” | commit 没成功落库 | 确认 commit 返回 `status: SUCCESS` |
