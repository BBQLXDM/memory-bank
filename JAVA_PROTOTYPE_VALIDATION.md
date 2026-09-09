# Memind Java 原型本地验证记录

## 1. 文档范围

本文记录如何使用 Java 原型验证 Memind 的本地运行链路。

这里的“Java 原型”包括：

- 使用 Java 21 自带的 `HttpClient` 直接验证 Chat 和 Embedding 模型接口；
- 使用 Java 21 自带的 `HttpClient` 调用 Memind 后端；
- 写入一段人工构造的对话；
- 检索生成的记忆；
- 查看 Dashboard 统计；
- 在前端查看 Raw Data 和 Items。

本文不包含 Benchmark 数据导入、批量 Session 调度和 QA 自动评分。相关工作是在本原型跑通后才开始的。

## 2. 本地组件

本地运行涉及以下组件：

| 组件 | 地址或位置 | 作用 |
| --- | --- | --- |
| Memind 后端 | `http://localhost:8366` | 提取、存储和检索记忆 |
| Memind 前端 | `http://localhost:5173` | 查看 Dashboard、Raw Data 和 Items |
| SQLite | `memind-server/data/memind-server.db` | 保存业务数据 |
| 本地向量存储 | `memind-server/data/vector-store.json` | 保存 Embedding 向量 |
| Chat 模型 | `.env` 中的 OpenAI 兼容配置 | Caption 和记忆提取 |
| Embedding 模型 | `.env` 中的 Embedding 配置 | 生成检索向量 |

本地环境使用 Java 21。项目 Java 版本通过 SDKMAN 加载。

## 3. Java 原型文件

原型工具统一放在：

```text
dev-tools/java
```

### 3.1 `ModelApiSmokeTest.java`

用于绕过 Memind，直接验证模型供应商接口：

- Chat API；
- Embedding API；
- API Key；
- Base URL；
- 模型名称；
- Java HTTP 客户端连通性。

支持模式：

```text
all
chat
embedding
```

### 3.2 `MemindApiSmokeTest.java`

用于验证 Memind 后端 API：

- `health`：健康检查；
- `extract`：写入一段测试对话并提取记忆；
- `retrieve`：检索测试用户的记忆；
- `dashboard`：查看总体数据统计；
- `all`：按顺序执行完整流程。

该工具不依赖额外第三方库，可以用 Java 21 单文件模式运行。

## 4. 启动服务

### 4.1 启动后端

```bash
cd /home/zzx/py/Memind-Local-Dev
source "$HOME/.sdkman/bin/sdkman-init.sh"
sdk env
set -a
source .env
set +a
mvn -pl memind-server spring-boot:run
```

启动成功标志：

```text
Tomcat started on port 8366
Started MemindServerApplication
```

### 4.2 启动前端

在另一个终端执行：

```bash
cd /home/zzx/py/Memind-Local-Dev/memind-ui
pnpm dev
```

浏览器访问：

```text
http://localhost:5173
```

完整启动说明另见：

```text
LOCAL_START_COMMANDS.md
```

## 5. 模型接口验证

进入项目目录并导出 `.env`：

```bash
cd /home/zzx/py/Memind-Local-Dev
set -a
source .env
set +a
```

运行：

```bash
java dev-tools/java/ModelApiSmokeTest.java all
```

实际验证结果：

```text
Chat model: qwen3.7-plus
HTTP status: 200
Result: PASS
```

Chat 模型按要求返回了 `OK`，自定义中文请求也能返回“连接正常”。

Embedding 验证结果：

```text
Embedding model: text-embedding-v4
HTTP status: 200
Result: PASS
```

响应包含浮点向量。这证明模型供应商的 Chat 和 Embedding 接口均可由 Java 正常访问。

## 6. 后端健康检查

运行：

```bash
cd /home/zzx/py/Memind-Local-Dev
java dev-tools/java/MemindApiSmokeTest.java health
```

实际结果：

```text
HTTP status: 200
Result: PASS
Response: {"data":{"status":"UP","service":"memind-server"}}
```

该结果证明 Spring Boot 后端已启动，并能接受 Open API 请求。

## 7. 人工测试数据

引入 Benchmark 之前，使用固定的人工对话验证完整链路。

默认标识：

```text
userId: local-test-user
agentId: local-test-agent
sourceClient: java-smoke-test
```

用户输入：

```text
我喜欢使用 Python 编程，并且通常在周日上午学习新技术。
```

助手输入：

```text
好的，我会记住这项信息。
```

该数据规模小、语义明确，适合验证：

- 对话是否成功接收；
- Caption 是否生成；
- 是否拆分为多个原子记忆；
- Embedding 是否生成；
- SQLite 和向量存储是否写入；
- 中文问题是否能召回记忆。

## 8. 写入记忆

运行：

```bash
java dev-tools/java/MemindApiSmokeTest.java extract
```

实际成功结果包含：

```text
HTTP status: 200
Result: PASS
status: SUCCESS
rawDataIds: 非空
itemIds: 非空
```

本次人工对话生成：

```text
Raw Data: 1 条
Items: 2 条
Insights: 0 条
```

原型阶段生成的两个记忆事实为：

```text
User likes using Python for programming
User usually learns new technologies on Sunday mornings
```

这证明 Chat 提取、Embedding、SQLite 和向量存储链路已经跑通。

## 9. 检索记忆

默认查询：

```text
用户喜欢使用什么编程语言，通常什么时候学习新技术？
```

运行：

```bash
java dev-tools/java/MemindApiSmokeTest.java retrieve
```

实际结果：

```text
HTTP status: 200
Result: PASS
```

响应的 `items` 中包含 Python 编程偏好和周日上午学习习惯。中文查询能够召回英文记忆，说明当前 Embedding 模型具备跨语言召回能力。

需要注意：`retrieve` 返回的是检索材料，不是由生成模型组织的一段最终自然语言答案。应用层仍需根据召回内容生成答案或执行规则判断。

## 10. Dashboard 和前端验证

查看总体统计：

```bash
java dev-tools/java/MemindApiSmokeTest.java dashboard
```

实际统计从零增长为：

```text
rawData > 0
items > 0
```

前端验证路径：

```text
/dashboard
/memories
/memories/{memoryId}/raw-data
/memories/{memoryId}/items
```

前端能够：

- 查看 Memory Workspace；
- 查看 Raw Data 原始对话和 Caption；
- 查看拆分后的 Items；
- 查看数据统计和其他记忆管理页面。

因此前端在原型阶段主要承担数据观察和管理作用，实际写入、检索验证由 Java 工具完成。

## 11. 自定义数据测试

无需修改 Java 文件，可以通过环境变量覆盖测试内容。

写入新的测试记忆：

```bash
MEMIND_TEST_MEMORY='我主要使用 Java 开发后端服务，偏好 Spring Boot，并使用 PostgreSQL 存储业务数据。' \
java dev-tools/java/MemindApiSmokeTest.java extract
```

检索该记忆：

```bash
MEMIND_TEST_QUERY='用户主要使用什么技术开发后端，使用什么数据库？' \
java dev-tools/java/MemindApiSmokeTest.java retrieve
```

使用独立用户：

```bash
MEMIND_TEST_USER_ID='user-002' \
MEMIND_TEST_AGENT_ID='agent-002' \
MEMIND_TEST_MEMORY='我喜欢在晚上阅读技术书籍，目前正在学习分布式系统。' \
java dev-tools/java/MemindApiSmokeTest.java extract
```

检索时必须使用相同的 `userId` 和 `agentId`，否则不能访问该工作区中的记忆。

## 12. 原型阶段发现并解决的问题

### 12.1 Base URL 问题

早期 Memind 提取返回：

```text
Retries exhausted: 3/3
```

后端日志显示 Chat 和 Vector 请求为 `404`。直接模型测试确认 API Key 和模型正常，问题最终定位为 OpenAI 兼容 Base URL 缺少 `/v1`。

修正后：

- Chat 测试通过；
- Embedding 测试通过；
- Memind 完整提取通过。

### 12.2 终端多行 JSON 粘贴损坏

早期使用长 `curl` 和 heredoc 时，终端粘贴导致 JSON 结构损坏。之后将请求封装到 Java 工具中，通过 `Object`/字符串构造请求并由程序发送，避免手工维护长 JSON。

### 12.3 Maven 执行目录错误

从 `/home/zzx/py` 执行 Maven 时，Maven 找不到 `memind-server` 模块。后续所有构建和运行命令统一从：

```text
/home/zzx/py/Memind-Local-Dev
```

执行。

### 12.4 中文内容被提取为英文

原型阶段观察到：

- 中文 Raw Data 输入保持中文；
- Caption 为英文；
- Items 初始也为英文；
- 中文查询仍可跨语言召回。

随后对 Caption 和 Memory Item Prompt 增加了“跟随源内容主要语言”的规则。后续测试显示 Memory Items 可以生成中文，但 Caption 仍受框架默认 English 规则影响。

该问题不影响基础写入和检索链路，但属于待继续处理的语言一致性问题。详细记录见：

```text
SOURCE_LANGUAGE_CHANGE_NOTES.md
```

### 12.5 同步接口耗时边界

人工测试数据很短，同步接口可以在 60 秒内完成。后续引入长 Session 的 Benchmark 后，同步接口会触发后端 60 秒超时，因此 Benchmark Runner 改用异步提取。

这项限制是 Benchmark 阶段才显现的，不影响本文记录的短对话 Java 原型。

## 13. 原型跑通的判定标准

本地 Java 原型达到以下条件，即认为跑通：

1. `ModelApiSmokeTest` 的 Chat 为 `PASS`；
2. `ModelApiSmokeTest` 的 Embedding 为 `PASS`；
3. `MemindApiSmokeTest health` 为 `PASS`；
4. `extract` 返回非空 `rawDataIds`；
5. `extract` 返回非空 `itemIds`；
6. `retrieve` 返回与输入事实相关的 Items；
7. Dashboard 中 Raw Data 和 Items 统计增加；
8. 前端可查看原始对话、Caption 和 Items；
9. SQLite 和本地向量存储能够持续保存数据。

以上条件均已实际验证。

## 14. 原型结论

在引入 Benchmark 之前，Java 原型已经证明：

```text
Java 21 客户端
→ OpenAI 兼容 Chat/Embedding
→ Memind Open API
→ 对话提取
→ Caption 和 Items
→ SQLite 与向量存储
→ 中文查询检索
→ 前端数据查看
```

整个本地链路可运行。

因此，后续 Benchmark 工作不是为了证明“Memind 能否启动”，而是进一步评估：

- 长 Session 的处理能力；
- 多 Session 时间线；
- 中文记忆质量；
- 知识更新；
- 时间推理；
- 拒答；
- 检索召回率和答案命中率。
