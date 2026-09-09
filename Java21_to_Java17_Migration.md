

````markdown C:\工作文件\企业数字伙伴\Code\Memind-nodata\Java21_to_Java17_Migration.md

## Java 21 → 17 降级影响分析

经过全面代码扫描，这是一个**影响较大但可控**的迁移。以下是详细分析：

---

### 一、统计总览

| 类别 | 影响范围 | 严重程度 |
|------|---------|---------|
| POM 版本声明 | 2 个文件 | 🟢 低 |
| Virtual Threads（虚拟线程） | 2 个文件，2 处 | 🔴 高 |
| SequencedCollections API（`getFirst()/getLast()`） | ~128 个文件，344 处 | 🟡 中 |
| CI/CD 配置 | 2 个文件 | 🟢 低 |
| SDKMAN 配置 | 1 个文件 | 🟢 低 |
| `record` / `var` / 其他 | 0（Java 17 兼容） | 🟢 无 |

---

### 二、详细分析

#### 1. POM 版本声明（2 处）

需要修改 `<java.version>` 从 `21` → `17`：

- [pom.xml:L75](file:///C:/工作文件/企业数字伙伴/Code/Memind-nodata/pom.xml#L75) — 根 POM
- [memind-dependencies/pom.xml:L62](file:///C:/工作文件/企业数字伙伴/Code/Memind-nodata/memind-dependencies/pom.xml#L62) — 依赖 BOM

> ⚠️ 注：`memind-clients/java` 已经使用 Java 17，无需修改。

#### 2. Virtual Threads 虚拟线程（🔴 最关键）

这是**唯一具有架构影响的改动**，涉及 2 个核心文件：

**文件 1：** [InsightBuildScheduler.java:L262](file:///C:/工作文件/企业数字伙伴/Code/Memind-nodata/memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/insight/scheduler/InsightBuildScheduler.java#L262)

```java
// Java 21 代码
this.executor = Executors.newVirtualThreadPerTaskExecutor();
```

**文件 2：** [InsightTreeReorganizer.java:L489](file:///C:/工作文件/企业数字伙伴/Code/Memind-nodata/memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/insight/tree/InsightTreeReorganizer.java#L489)

```java
// Java 21 代码
var t = Thread.ofVirtual().start(() -> resummarizeRootAndReset(memoryId, p, language));
```

**为什么危险：**
- Virtual threads 是 Java 21 的核心特性，可轻松创建百万级轻量线程
- 项目注释明确写着："Full link synchronization blocking, designed to run on virtual threads"
- 降级为 `Executors.newFixedThreadPool(n)` 或 `Executors.newCachedThreadPool()` 需要合理设置线程池大小
- 如果线程池太小，可能造成 Insight 树重建任务排队阻塞
- 如果线程池太大，平台线程的内存开销远大于虚拟线程

#### 3. SequencedCollections API（🟡 量大但机械）

Java 21 为 `List` 接口新增了 `getFirst()` 和 `getLast()` 方法。在 Java 17 中需要替换：

| 原代码 | 替换为 |
|--------|--------|
| `list.getFirst()` | `list.get(0)` |
| `list.getLast()` | `list.get(list.size() - 1)` |

**统计：**
- `getFirst()`：**303 处**，分布在约 100 个文件
- `getLast()`：**41 处**，分布在约 28 个文件

主要分布在：
- `memind-core` 的 extraction/retrieval 模块（主代码 + 测试）
- `memind-plugins` 各 rawdata 插件
- `memind-server` 测试

#### 4. 不受影响的部分 ✅

| 特性 | Java 版本 | 状态 |
|------|----------|------|
| `record` 关键字 | Java 16 GA | ✅ 兼容 |
| `var` 局部变量 | Java 10 | ✅ 兼容 |
| Text Blocks (`"""`) | Java 15 | ✅ 兼容 |
| `instanceof` 模式匹配 | Java 16 | ✅ 兼容 |
| Switch 表达式 | Java 14 | ✅ 兼容 |
| Spring Boot 4.1.0 | 要求 Java 17+ | ✅ 兼容 |
| Spring AI 2.0.0 | 要求 Java 17+ | ✅ 兼容 |

---

### 三、改动工作量评估

#### 方案 A：纯 Java 17 迁移（推荐）

| 步骤 | 工作量 | 说明 |
|------|--------|------|
| POM 版本号 | 5 分钟 | 改 2 个文件的 `java.version` |
| virtual threads 替换 | 30 分钟 | 改为 `newFixedThreadPool(n)`，需评估 `n` 值 |
| `getFirst()` → `get(0)` | 1-2 小时 | 可用 IDE 批量替换（正则），约 300 处 |
| `getLast()` → `get(size-1)` | 15 分钟 | 约 40 处，IDE 批量替换 |
| CI/CD 配置 | 5 分钟 | 改 2 个 yml 文件 |
| 编译验证 | 15 分钟 | `mvn compile` |
| 测试验证 | 取决于测试覆盖 | 已有大量测试，跑一遍即可 |

**总预估：约 2-5 小时**（不含测试运行时间）

#### 方案 B：使用 `--enable-preview`（不推荐）

Java 17 不支持 virtual threads 的 preview，此方案不适用。

---

### 四、结论

**影响较大，但主要是工作量问题，不是技术可行性问题。**

- ⚠️ **唯一真正的风险点**：Virtual Threads → 固定线程池的替换，需要合理评估 Insight 树重建的并发量，设置合适的线程池大小
- 📝 **大量机械性替换**：`getFirst()`/`getLast()` 虽然数量多（344 处），但都是纯文本替换，IDE 正则即可完成
- ✅ **项目其他部分完全兼容 Java 17**：record、var、Spring Boot 4.x 等均无问题



# Java 21 → 17 迁移方案

> - **项目**：Memind (memind-nodata)
> - **迁移目标**：将 Java 版本从 21 降级为 17
> - **预计工作量**：2-5 小时
> - **执行日期**：_______

---

## 改动总览

| 类别 | 文件数 | 处数 | 严重程度 |
|------|--------|------|---------|
| POM 版本声明 | 2 | 2 | 🟢 低 |
| Virtual Threads 虚拟线程 | 2 | 2 | 🔴 高 |
| Pattern Matching for Switch | 4 | 11 | 🔴 高 |
| `getFirst()` → `get(0)` 主代码 | 6 | 14 | 🟡 中 |
| `getLast()` → `get(size()-1)` 主代码 | 15 | 20 | 🟡 中 |
| `getFirst()` → `get(0)` 测试代码 | ~94 | 289 | 🟢 低 |
| `getLast()` → `get(size()-1)` 测试代码 | ~14 | 21 | 🟢 低 |
| CI/CD 配置 | 2 | 4 | 🟢 低 |
| SDKMAN 配置 | 1 | 1 | 🟢 低 |
| **合计** | **~140** | **364** | — |

---

## 迁移执行顺序

````
1. POM 文件         ── 5 分钟
2. Virtual Threads  ── 30 分钟
3. Pattern Matching ── 30 分钟
4. getFirst() 主代码 ── 15 分钟
5. getLast() 主代码  ── 20 分钟
6. 测试代码批量替换   ── 1-2 小时
7. CI/CD 配置        ── 5 分钟
8. SDKMAN 配置       ── 1 分钟
9. 编译验证          ── 15 分钟
10. 测试验证         ── 视测试数量而定
```

---

## 一、POM 文件（2 个文件）

### 1.1 根 POM — `pom.xml`

**位置**：`<properties>` 块，第 75 行

```diff
- <java.version>21</java.version>
+ <java.version>17</java.version>
```

### 1.2 依赖管理 POM — `memind-dependencies/pom.xml`

**位置**：`<properties>` 块，第 62 行

```diff
- <java.version>21</java.version>
+ <java.version>17</java.version>
```

> ℹ️ `memind-clients/java/pom.xml` 已使用 Java 17，无需修改。

---

## 二、Virtual Threads 虚拟线程（2 个文件，2 处）

> ⚠️ **高风险**：虚拟线程是 Java 21 正式特性，Java 17 中不可用。需替换为传统线程池/线程，原项目注释写明 "Full link synchronization blocking, designed to run on virtual threads"，降级后需关注线程资源使用情况。

### 2.1 InsightBuildScheduler.java

**文件**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/insight/scheduler/InsightBuildScheduler.java`

**行号**：第 262 行

```diff
- this.executor = Executors.newVirtualThreadPerTaskExecutor();
+ this.executor = Executors.newCachedThreadPool();
```

> 💡 `newCachedThreadPool()` 会按需创建线程并复用空闲线程，语义上最接近虚拟线程的逐任务新建模式。如需控制并发上限，可改为 `newFixedThreadPool(n)`，其中 `n` 参考 `config.concurrency()` 配置值。

### 2.2 InsightTreeReorganizer.java

**文件**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/insight/tree/InsightTreeReorganizer.java`

**行号**：第 489 行

```diff
- var t = Thread.ofVirtual().start(() -> resummarizeRootAndReset(memoryId, p, language));
+ var t = new Thread(() -> resummarizeRootAndReset(memoryId, p, language));
+ t.start();
```

> 💡 原代码 `Thread.ofVirtual().start()` 是创建并启动虚拟线程的单行写法，拆分为 `new Thread()` + `t.start()` 两步。

---

## 三、Pattern Matching for Switch（4 个文件，11 处）

> ⚠️ **高风险**：`case Type var ->` 语法是 Java 21 正式特性（Java 17 中仅为 preview 功能，`--release 17` 编译选项下不可用），需改为 `if-else` + `instanceof` 模式匹配（`instanceof` 模式匹配本身是 Java 16 正式特性，可用）。

### 3.1 OpenTelemetryMemoryObserver.java

**文件**：`memind-plugins/memind-plugin-tracing-opentelemetry/src/main/java/com/openmemind/ai/memory/plugin/tracing/otel/OpenTelemetryMemoryObserver.java`

**行号**：第 166-170 行（共 5 个 case 分支）

```diff
- switch (value) {
-     case String s -> builder.put(AttributeKey.stringKey(key), s);
-     case Long l -> builder.put(AttributeKey.longKey(key), l);
-     case Integer i -> builder.put(AttributeKey.longKey(key), i.longValue());
-     case Double d -> builder.put(AttributeKey.doubleKey(key), d);
-     case Boolean b -> builder.put(AttributeKey.booleanKey(key), b);
-     default -> builder.put(AttributeKey.stringKey(key), value.toString());
- }
+ if (value instanceof String s) {
+     builder.put(AttributeKey.stringKey(key), s);
+ } else if (value instanceof Long l) {
+     builder.put(AttributeKey.longKey(key), l);
+ } else if (value instanceof Integer i) {
+     builder.put(AttributeKey.longKey(key), i.longValue());
+ } else if (value instanceof Double d) {
+     builder.put(AttributeKey.doubleKey(key), d);
+ } else if (value instanceof Boolean b) {
+     builder.put(AttributeKey.booleanKey(key), b);
+ } else {
+     builder.put(AttributeKey.stringKey(key), value.toString());
+ }
```

### 3.2 RawDataConverter.java

**文件**：`memind-plugins/memind-plugin-spring-boot-starters/memind-plugin-mybatis-plus-starter/src/main/java/com/openmemind/ai/memory/plugin/store/mybatis/converter/RawDataConverter.java`

**行号**：第 96-101 行（共 2 个 case 分支，无 default）

```diff
- switch (durableSegment.boundary()) {
-     case CharBoundary cb -> {
-         boundaryMap.put("type", "char");
-         boundaryMap.put("startChar", cb.startChar());
-         boundaryMap.put("endChar", cb.endChar());
-     }
-     case MessageBoundary mb -> {
-         boundaryMap.put("type", "message");
-         boundaryMap.put("startMessage", mb.startMessage());
-         boundaryMap.put("endMessage", mb.endMessage());
-     }
- }
+ if (durableSegment.boundary() instanceof CharBoundary cb) {
+     boundaryMap.put("type", "char");
+     boundaryMap.put("startChar", cb.startChar());
+     boundaryMap.put("endChar", cb.endChar());
+ } else if (durableSegment.boundary() instanceof MessageBoundary mb) {
+     boundaryMap.put("type", "message");
+     boundaryMap.put("startMessage", mb.startMessage());
+     boundaryMap.put("endMessage", mb.endMessage());
+ }
```

### 3.3 SqliteMemoryStore.java

**文件**：`memind-plugins/memind-plugin-jdbc/memind-plugin-jdbc-sqlite/src/main/java/com/openmemind/ai/memory/plugin/jdbc/sqlite/SqliteMemoryStore.java`

**行号**：第 1484-1489 行（共 2 个 case 分支 + default）

```diff
- switch (durableSegment.boundary()) {
-     case CharBoundary charBoundary -> {
-         boundaryMap.put("type", "char");
-         boundaryMap.put("startChar", charBoundary.startChar());
-         boundaryMap.put("endChar", charBoundary.endChar());
-     }
-     case MessageBoundary messageBoundary -> {
-         boundaryMap.put("type", "message");
-         boundaryMap.put("startMessage", messageBoundary.startMessage());
-         boundaryMap.put("endMessage", messageBoundary.endMessage());
-     }
-     default -> {}
- }
+ if (durableSegment.boundary() instanceof CharBoundary charBoundary) {
+     boundaryMap.put("type", "char");
+     boundaryMap.put("startChar", charBoundary.startChar());
+     boundaryMap.put("endChar", charBoundary.endChar());
+ } else if (durableSegment.boundary() instanceof MessageBoundary messageBoundary) {
+     boundaryMap.put("type", "message");
+     boundaryMap.put("startMessage", messageBoundary.startMessage());
+     boundaryMap.put("endMessage", messageBoundary.endMessage());
+ }
```

> 💡 原 `default -> {}` 为空操作，`if-else` 中无需显式写 else 块。

### 3.4 PostgresqlMemoryStore.java

**文件**：`memind-plugins/memind-plugin-jdbc/memind-plugin-jdbc-postgresql/src/main/java/com/openmemind/ai/memory/plugin/jdbc/postgresql/PostgresqlMemoryStore.java`

**行号**：第 1468-1473 行（共 2 个 case 分支 + default，与 Sqlite 版本代码完全一致）

```diff
- switch (durableSegment.boundary()) {
-     case CharBoundary charBoundary -> {
-         boundaryMap.put("type", "char");
-         boundaryMap.put("startChar", charBoundary.startChar());
-         boundaryMap.put("endChar", charBoundary.endChar());
-     }
-     case MessageBoundary messageBoundary -> {
-         boundaryMap.put("type", "message");
-         boundaryMap.put("startMessage", messageBoundary.startMessage());
-         boundaryMap.put("endMessage", messageBoundary.endMessage());
-     }
-     default -> {}
- }
+ if (durableSegment.boundary() instanceof CharBoundary charBoundary) {
+     boundaryMap.put("type", "char");
+     boundaryMap.put("startChar", charBoundary.startChar());
+     boundaryMap.put("endChar", charBoundary.endChar());
+ } else if (durableSegment.boundary() instanceof MessageBoundary messageBoundary) {
+     boundaryMap.put("type", "message");
+     boundaryMap.put("startMessage", messageBoundary.startMessage());
+     boundaryMap.put("endMessage", messageBoundary.endMessage());
+ }
```

---

## 四、`getFirst()` → `get(0)` 主代码文件（6 个文件，14 处）

> `List.getFirst()` 是 Java 21 新增的 SequencedCollection API 方法，Java 17 中需用 `get(0)` 替代，语义完全等价。

### 4.1 MybatisPlusMemoryStore.java
**路径**：`memind-plugins/memind-plugin-spring-boot-starters/memind-plugin-mybatis-plus-starter/src/main/java/com/openmemind/ai/memory/plugin/store/mybatis/MybatisPlusMemoryStore.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 632 | `values.getFirst() == null` | `values.get(0) == null` |
| 635 | `Object value = values.getFirst()` | `Object value = values.get(0)` |

### 4.2 CsvTextShaper.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/parser/CsvTextShaper.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 96 | `CSVRecord header = rows.getFirst()` | `CSVRecord header = rows.get(0)` |

### 4.3 CsvRowWindowDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/CsvRowWindowDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 98 | `rows.getFirst().segment()` | `rows.get(0).segment()` |
| 101 | `rows.getFirst().segment()` | `rows.get(0).segment()` |
| 118 | `rows.getFirst().rowNumber()` | `rows.get(0).rowNumber()` |
| 173 | `markers.getFirst().start()` | `markers.get(0).start()` |

### 4.4 MarkdownDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/MarkdownDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 100 | `headings.getFirst().start() > 0` | `headings.get(0).start() > 0` |
| 101 | `0, headings.getFirst().start()` | `0, headings.get(0).start()` |

### 4.5 ParagraphWindowDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/ParagraphWindowDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 64 | `normalized.getFirst()` | `normalized.get(0)` |
| 92 | `segments.getFirst()` | `segments.get(0)` |

### 4.6 PdfPageDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/PdfPageDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 119 | `pages.getFirst().segment()` | `pages.get(0).segment()` |
| 122 | `pages.getFirst().segment()` | `pages.get(0).segment()` |
| 129 | `pages.getFirst().pageNumber()` | `pages.get(0).pageNumber()` |
| 151 | `markers.getFirst().start()` | `markers.get(0).start()` |

---

## 五、`getLast()` → `get(size()-1)` 主代码文件（15 个文件，20 处）

> `List.getLast()` 同样是 Java 21 SequencedCollection API，需用 `get(list.size() - 1)` 替代，语义等价。

### 5.1 ToolCallChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-toolcall/src/main/java/com/openmemind/ai/memory/plugin/rawdata/toolcall/chunk/ToolCallChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 169 | `records.getLast().calledAt()` | `records.get(records.size() - 1).calledAt()` |
| 170 | `records.getLast().calledAt()` | `records.get(records.size() - 1).calledAt()` |

### 5.2 ParagraphWindowDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/ParagraphWindowDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 70 | `current.getLast()` | `current.get(current.size() - 1)` |
| 93 | `segments.getLast()` | `segments.get(segments.size() - 1)` |

### 5.3 PdfPageDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/PdfPageDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 123 | `pages.getLast().segment()` | `pages.get(pages.size() - 1).segment()` |
| 130 | `pages.getLast().pageNumber()` | `pages.get(pages.size() - 1).pageNumber()` |

### 5.4 CsvRowWindowDocumentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/chunk/CsvRowWindowDocumentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 102 | `rows.getLast().segment()` | `rows.get(rows.size() - 1).segment()` |
| 120 | `rows.getLast().rowNumber()` | `rows.get(rows.size() - 1).rowNumber()` |

### 5.5 CsvTextShaper.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-document/src/main/java/com/openmemind/ai/memory/plugin/rawdata/document/parser/CsvTextShaper.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 71 | `rows.getLast().isEmpty()` | `rows.get(rows.size() - 1).isEmpty()` |

### 5.6 TranscriptSegmentChunker.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-audio/src/main/java/com/openmemind/ai/memory/plugin/rawdata/audio/chunk/TranscriptSegmentChunker.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 110 | `content.segments().getLast()` | `content.segments().get(content.segments().size() - 1)` |
| 317 | `slices.getLast().transcriptSegment()` | `slices.get(slices.size() - 1).transcriptSegment()` |
| 319 | `slices.getLast().segment()` | `slices.get(slices.size() - 1).segment()` |

### 5.7 AgentEpisodeAssembler.java
**路径**：`memind-plugins/memind-plugin-rawdatas/memind-plugin-rawdata-agent/src/main/java/com/openmemind/ai/memory/plugin/rawdata/agent/chunk/AgentEpisodeAssembler.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 145 | `eventIds.getLast()` | `eventIds.get(eventIds.size() - 1)` |

### 5.8 EntityVariantKeyGenerator.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/item/graph/entity/resolve/EntityVariantKeyGenerator.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 99 | `tokens.getLast()` | `tokens.get(tokens.size() - 1)` |

### 5.9 ThreadHeadlineFormatter.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/thread/ThreadHeadlineFormatter.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 62 | `participants.getLast()` | `participants.get(participants.size() - 1)` |

### 5.10 ThreadProjectionMaterializer.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/thread/ThreadProjectionMaterializer.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 282 | `items.getLast().id()` | `items.get(items.size() - 1).id()` |

### 5.11 ThreadStructuralReducer.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/thread/ThreadStructuralReducer.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 66 | `events.getLast().eventTime()` | `events.get(events.size() - 1).eventTime()` |

### 5.12 TokenAwareSegmentAssembler.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/rawdata/chunk/TokenAwareSegmentAssembler.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 240 | `candidates.getLast()` | `candidates.get(candidates.size() - 1)` |

### 5.13 AdaptiveTruncator.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/retrieval/truncation/AdaptiveTruncator.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 92 | `truncated.getLast().finalScore()` | `truncated.get(truncated.size() - 1).finalScore()` |

### 5.14 CommitDetectionInput.java
**路径**：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/context/CommitDetectionInput.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 61 | `history.getLast().timestamp()` | `history.get(history.size() - 1).timestamp()` |

### 5.15 MemindAdapter.java
**路径**：`memind-evaluation/src/main/java/com/openmemind/ai/memory/evaluation/adapter/memind/MemindAdapter.java`

| 行号 | 原代码 | 替换为 |
|------|--------|--------|
| 512 | `answerLines.getLast().isBlank()` | `answerLines.get(answerLines.size() - 1).isBlank()` |

---

## 六、CI/CD 配置（2 个文件）

### 6.1 构建流水线 — `.github/workflows/maven.yml`

**行号**：第 26-29 行

```diff
- - name: Set up JDK 21
+ - name: Set up JDK 17
    uses: actions/setup-java@v4
    with:
-     java-version: '21'
+     java-version: '17'
      distribution: 'temurin'
      cache: maven
```

### 6.2 发布流水线 — `.github/workflows/release-maven.yml`

**行号**：第 63-66 行

```diff
- - name: Set up JDK 21
+ - name: Set up JDK 17
    uses: actions/setup-java@v4
    with:
-     java-version: '21'
+     java-version: '17'
      distribution: temurin
      cache: maven
```

> ℹ️ `release-java-client.yml` 已使用 JDK 17，无需修改。

---

## 七、SDKMAN 配置（1 个文件）

### 7.1 `.sdkmanrc`

```diff
- java=21.0.12-ms
+ java=17.0.12-tem
```

---

## 八、测试文件批量替换清单

### 8.1 `getFirst()` → `get(0)` 测试文件

> 全部在 `src/test/` 目录下，共约 94 个文件、289 处。可在 IDE 中执行全项目正则替换：
>
> **查找**：`\.getFirst\(\)`
> **替换为**：`.get(0)`
>
> ⚠️ 注意：替换前请确认匹配项均为 `List.getFirst()` 调用，排除可能的同名自定义方法。

| 模块 | 文件 | 处数 |
|------|------|------|
| memind-server | `MemindServerIntegrationTest.java` | 3 |
| memind-plugins/mybatis-plus-starter | `MybatisPlusMemoryThreadStoreTest.java` | 1 |
| | `MybatisGraphReadParityTest.java` | 4 |
| | `MybatisGraphOperationsTest.java` | 5 |
| | `DatabaseDialectScriptResourceTest.java` | 1 |
| memind-plugins/jdbc-starter | `JdbcPluginAutoConfigurationSqliteTest.java` | 1 |
| memind-plugins/rawdata-image | `ImageContentProcessorTest.java` | 4 |
| | `ImageExtractionPipelineIntegrationTest.java` | 1 |
| memind-plugins/rawdata-toolcall | `LlmToolCallItemExtractionStrategyTest.java` | 2 |
| memind-plugins/rawdata-document | `DocumentRawDataPluginTest.java` | 1 |
| | `DocumentContentProcessorTest.java` | 8 |
| | `ParagraphWindowDocumentChunkerTest.java` | 2 |
| | `PdfPageDocumentChunkerTest.java` | 2 |
| | `LlmDocumentCaptionGeneratorTest.java` | 1 |
| memind-plugins/rawdata-audio | `TranscriptSegmentChunkerTest.java` | 7 |
| | `AudioContentProcessorTest.java` | 4 |
| memind-plugins/rawdata-agent | `AgentItemExtractionStrategyTest.java` | 1 |
| | `AgentEpisodeAssemblerTest.java` | 3 |
| 其他约 76 个测试文件 | 见完整 grep 扫描结果 | ~239 |

### 8.2 `getLast()` → `get(size()-1)` 测试文件

> 共约 14 个文件、21 处。由于替换涉及变量名，**无法用简单正则批量替换**，需逐个文件手动修改。
>
> **替换规则**：`xxx.getLast()` → `xxx.get(xxx.size() - 1)`

| 模块 | 文件 | 处数 |
|------|------|------|
| memind-core | `LlmConversationChunkerTest.java` | 2 |
| | `ConversationChunkerTest.java` | 1 |
| | `LlmSelfVerificationStepTest.java` | 1 |
| | `LlmInsightGeneratorTest.java` | 3 |
| | `RerankerTest.java` | 1 |
| | `ResultMergerTest.java` | 3 |
| | `LlmLongQueryCondenserTest.java` | 1 |
| | `SimpleRetrievalStrategyTest.java` | 1 |
| | `LlmTypedQueryExpanderTest.java` | 1 |
| memind-plugins/rawdata-document | `MarkdownDocumentChunkerTest.java` | 1 |
| | `LlmDocumentCaptionGeneratorTest.java` | 2 |
| memind-plugins/rawdata-audio | `TranscriptSegmentChunkerTest.java` | 1 |
| memind-plugins/rawdata-agent | `AgentCaptionGeneratorTest.java` | 1 |

---

## 九、不受影响的特性（Java 17 兼容确认）

| 特性 | 引入版本 | 状态 |
|------|----------|------|
| `record` 关键字 | Java 16 GA | ✅ 兼容 |
| `var` 局部变量类型推断 | Java 10 | ✅ 兼容 |
| Text Blocks `"""` | Java 15 | ✅ 兼容 |
| `instanceof` 模式匹配 `x instanceof Type t` | Java 16 | ✅ 兼容 |
| Switch 表达式 `switch { case X -> ... }` | Java 14 | ✅ 兼容 |
| `Stream.toList()` | Java 16 | ✅ 兼容 |
| `List.of()` / `Set.of()` / `Map.of()` | Java 9 | ✅ 兼容 |
| `Comparator.reversed()` | Java 8 | ✅ 兼容 |
| `HttpClient` | Java 11 | ✅ 兼容 |
| `String.formatted()` | Java 15 | ✅ 兼容 |
| `String.stripIndent()` / `translateEscapes()` | Java 15 | ✅ 兼容 |
| Spring Boot 4.1.0 | 要求 Java 17+ | ✅ 兼容 |
| Spring AI 2.0.0 | 要求 Java 17+ | ✅ 兼容 |
| MyBatis-Plus 3.5.16 | 要求 Java 8+ | ✅ 兼容 |

---

## 十、验证命令

完成所有修改后，按顺序执行以下命令进行验证：

```bash
# 1. 清理并编译
mvn clean compile

# 2. 运行全部测试
mvn test

# 3. 打包验证
mvn package -DskipTests
```

---

## 十一、风险提示

| 风险项 | 说明 | 建议 |
|--------|------|------|
| Virtual Threads 降级 | 原代码设计假定可创建大量轻量虚拟线程（百万级），降级为 `newCachedThreadPool()` 后平台线程有内存开销 | 在 Insight 树重建场景下监控线程数，必要时以 `newFixedThreadPool(config.concurrency())` 限制并发 |
| Pattern Matching 穷尽性 | `switch` 的模式匹配具有编译期穷尽性检查，改为 `if-else` 后失去此检查 | 确保所有 Boundary 子类型分支覆盖完整，建议在 `else` 块加日志 |
| `getLast()` 空列表 | `get(size()-1)` 在空列表上抛 `IndexOutOfBoundsException`，与原 `getLast()` 抛 `NoSuchElementException` 不同 | 检查所有调用方是否已做空列表前置检查，本项目代码中均已做检查，无需额外处理 |
| 测试覆盖率 | 大量测试文件改动，需确保测试通过 | 改完后全量跑 `mvn test`，逐一修复失败的测试 |

---

## 十二、改动完成检查清单

- [ ] `pom.xml` — `java.version` 改为 17
- [ ] `memind-dependencies/pom.xml` — `java.version` 改为 17
- [ ] `InsightBuildScheduler.java` — virtual thread 替换
- [ ] `InsightTreeReorganizer.java` — virtual thread 替换
- [ ] `OpenTelemetryMemoryObserver.java` — pattern matching 替换
- [ ] `RawDataConverter.java` — pattern matching 替换
- [ ] `SqliteMemoryStore.java` — pattern matching 替换
- [ ] `PostgresqlMemoryStore.java` — pattern matching 替换
- [ ] 6 个主代码文件 — `getFirst()` → `get(0)`
- [ ] 15 个主代码文件 — `getLast()` → `get(size()-1)`
- [ ] ~94 个测试文件 — `getFirst()` → `get(0)` 批量替换
- [ ] ~14 个测试文件 — `getLast()` → `get(size()-1)` 手动替换
- [ ] `.github/workflows/maven.yml` — JDK 17
- [ ] `.github/workflows/release-maven.yml` — JDK 17
- [ ] `.sdkmanrc` — Java 17
- [ ] `mvn clean compile` 通过
- [ ] `mvn test` 通过
```

---

以上是完整的 Markdown 文档。由于我处于 **Chat 模式**，无法直接创建文件。请您：

1. 全选复制上述代码块中的所有内容
2. 在 `C:\工作文件\企业数字伙伴\Code\Memind-nodata` 目录下新建文件 `Java21_to_Java17_Migration.md`
3. 粘贴并保存

如需我直接执行文件创建，请在输入框中输入 **@Builder** 切换模式。