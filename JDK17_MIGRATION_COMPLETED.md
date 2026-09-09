# JDK 21 → 17 迁移：实际完成改动记录

> 状态：**已完成并验证通过**（2026-09-08）
> 配套方案文档：`Java21_to_Java17_Migration.md`（迁移前的分析计划）
> 本文记录的是**实际落地**的改动与验证结果，与计划文档的差异处已标注。

---

## 一、验证结果总览

| 验证项 | 结果 |
|--------|------|
| `mvn clean install`（JDK 17 全量构建） | ✅ BUILD SUCCESS |
| 后端真实启动（`spring-boot:run`） | ✅ Tomcat started（验证期间因原版实例占用 8366，临时用 18366 覆盖启动） |
| 健康检查 `GET /open/v1/health` | ✅ `{"status":"UP"}` |
| 冒烟测试 Health / Extract / Retrieve / Dashboard | ✅ 4/4 PASS |
| 自定义记忆写入 + 查询（写入→检索闭环） | ✅ PASS |
| JDK 21 API 残留扫描（`getFirst`/`getLast`/`removeLast`/虚拟线程） | ✅ 无残留 |

> 端口说明：迁移验证是在本机与原版并存的情况下进行的，因此验证时后端临时起在
> 18366（`export SERVER_PORT=18366` + 测试脚本默认地址同步调整）。**项目默认端口
> 保持 8366**，验证完成后相关配置已全部还原，日常按 `LOCAL_START_COMMANDS.md`
> 直接启动即为 8366。

---

## 二、构建配置改动

### 2.1 根 POM — `pom.xml`（第 75 行）

```diff
- <java.version>21</java.version>
+ <java.version>17</java.version>
```

同时 `<maven-compiler-plugin>` 使用 `<release>${java.version}</release>`（第 249 行），无需额外修改。

### 2.2 依赖 BOM — `memind-dependencies/pom.xml`（第 62 行）

```diff
- <java.version>21</java.version>
+ <java.version>17</java.version>
```

### 2.3 `.sdkmanrc`

```diff
- java=21.0.12-ms
+ java=17.0.20-apt
```

> ⚠️ 本机 `~/.sdkman/candidates/java` 目录归 root 所有，SDKMAN 无法安装该版本，
> **启动时不要用 `sdk env`**，改用 `export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`。
> 此行仅作版本记录。

---

## 三、虚拟线程替换（2 处，计划文档中的高风险项）

### 3.1 InsightBuildScheduler.java

文件：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/insight/scheduler/InsightBuildScheduler.java`

实际改动（第 262 行）：

```diff
- this.executor = Executors.newVirtualThreadPerTaskExecutor();
+ this.executor = Executors.newCachedThreadPool();
```

> 计划文档推荐 `newCachedThreadPool()`，实际采纳。理由：按需创建、复用空闲线程，
> 语义上最接近虚拟线程的逐任务模式，不限制 Insight 树重建的并发排队。

### 3.2 InsightTreeReorganizer.java

文件：`memind-core/src/main/java/com/openmemind/ai/memory/core/extraction/insight/tree/InsightTreeReorganizer.java`

实际改动（第 489 行）：

```diff
- var t = Thread.ofVirtual().start(() -> resummarizeRootAndReset(memoryId, p, language));
+ var t = new Thread(() -> resummarizeRootAndReset(memoryId, p, language));
+ t.start();
```

---

## 四、switch 模式匹配 → if-else（4 处，计划文档中的高风险项）

`case Type var ->` 是 Java 21 正式特性，`--release 17` 下不可用，全部改为
`instanceof` 模式匹配（Java 16 正式特性）。

### 4.1 OpenTelemetryMemoryObserver.java（第 165 行起，5 分支）

文件：`memind-plugins/memind-plugin-tracing-opentelemetry/src/main/java/com/openmemind/ai/memory/plugin/tracing/otel/OpenTelemetryMemoryObserver.java`

```java
if (value instanceof String s) {
    builder.put(AttributeKey.stringKey(key), s);
} else if (value instanceof Long l) {
    builder.put(AttributeKey.longKey(key), l);
} else if (value instanceof Integer i) {
    builder.put(AttributeKey.longKey(key), i.longValue());
} else if (value instanceof Double d) {
    builder.put(AttributeKey.doubleKey(key), d);
} else if (value instanceof Boolean b) {
    builder.put(AttributeKey.booleanKey(key), b);
} else {
    builder.put(AttributeKey.stringKey(key), value.toString());
}
```

### 4.2 RawDataConverter.java（第 95-99 行）

文件：`memind-plugins/memind-plugin-spring-boot-starters/memind-plugin-mybatis-plus-starter/src/main/java/com/openmemind/ai/memory/plugin/store/mybatis/converter/RawDataConverter.java`

```java
if (durableSegment.boundary() instanceof CharBoundary cb) {
    boundaryMap.put("type", "char");
    boundaryMap.put("startChar", cb.startChar());
    boundaryMap.put("endChar", cb.endChar());
} else if (durableSegment.boundary() instanceof MessageBoundary mb) {
    boundaryMap.put("type", "message");
    boundaryMap.put("startMessage", mb.startMessage());
    boundaryMap.put("endMessage", mb.endMessage());
}
```

### 4.3 SqliteMemoryStore.java（第 1483-1487 行）

文件：`memind-plugins/memind-plugin-jdbc/memind-plugin-jdbc-sqlite/src/main/java/com/openmemind/ai/memory/plugin/jdbc/sqlite/SqliteMemoryStore.java`

同 4.2 的 if-else 写法（原 `default -> {}` 空分支直接省略）。

### 4.4 PostgresqlMemoryStore.java（第 1467-1471 行）

文件：`memind-plugins/memind-plugin-jdbc/memind-plugin-jdbc-postgresql/src/main/java/com/openmemind/ai/memory/plugin/jdbc/postgresql/PostgresqlMemoryStore.java`

同 4.2 的 if-else 写法。

---

## 五、SequencedCollection API 批量替换（~128 个文件，344 处）

| 原代码 | 替换为 |
|--------|--------|
| `list.getFirst()` | `list.get(0)` |
| `list.getLast()` | `list.get(list.size() - 1)` |

- **主代码**：21 个文件 34 处（memind-core 的 extraction/retrieval、document/toolcall/audio/agent 插件、evaluation），逐处替换
- **测试代码**：~108 个文件 310 处，正则批量替换 `\.getFirst\(\)` → `.get(0)`，
  `getLast()` 因涉及变量名逐文件替换为 `.get(xxx.size() - 1)`
- **行为差异说明**：空列表时 `get(size()-1)` 抛 `IndexOutOfBoundsException`
  （原 `getLast()` 抛 `NoSuchElementException`），所有调用方均有空列表前置检查，无影响

替换后全仓扫描 `getFirst()/getLast()/removeLast()`：**0 残留**。

---

## 六、CI/CD 配置（3 个文件，全部已指向 17）

| 文件 | 状态 |
|------|------|
| `.github/workflows/maven.yml`（第 29 行） | ✅ `java-version: '17'` |
| `.github/workflows/release-maven.yml`（第 66 行） | ✅ `java-version: '17'` |
| `.github/workflows/release-java-client.yml`（第 58 行） | ✅ 原本就是 17，未改 |

---

## 七、随迁移一并调整的本地配置

| 文件 | 改动 | 原因 |
|------|------|------|
| `memind-server/src/main/resources/application.yml` 第 76 行 | 保持默认 `port: ${SERVER_PORT:8366}` | 与原版项目一致；迁移验证期间曾临时改为 18366 并以环境变量覆盖启动，验证完成后已还原为 8366 |
| `memind-ui/vite.config.ts` 第 33 行 | 保持代理 `target: "http://localhost:8366"` | 与后端默认端口对齐（18366 为临时覆盖端口，已还原） |

---

## 八、迁移过程中踩过的坑（重要，启动前必读）

### 8.1 「类文件具有错误的版本 65.0, 应为 61.0」

**现象**：用 JDK 17 编译/启动时，大量 `无法访问 com.openmemind.ai.memory.core...` 报错，
提示 memind-core jar 中类文件版本是 65.0（Java 21）而期望 61.0（Java 17）。

**原因**：`~/.m2/repository` 里缓存着迁移前用 JDK 21 `mvn install` 的旧
`memind-core-0.2.0-SNAPSHOT.jar`。`mvn -pl memind-server spring-boot:run` 不带 `-am`
时不会重编 memind-core，直接拉旧包。

**解决**（一次性）：

```bash
rm -rf ~/.m2/repository/com/openmemind/ai
cd /home/zzx/py/Memind-jdk17
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
mvn clean install -DskipTests -Dlicense.skip=true -Dcheckstyle.skip=true
```

**预防**：改过 core/plugins 代码后，启动前先重新 `mvn install`，
或启动时加 `-am` 让 Maven 在 reactor 内现编依赖（见启动文档）。

### 8.2 前端 `vite: not found`

新拷贝目录没有 `node_modules`，与 JDK 迁移无关，进入 `memind-ui` 先 `pnpm install`。

---

## 九、与计划文档的差异

| 项 | 计划 | 实际 |
|----|------|------|
| InsightBuildScheduler 线程池 | `newCachedThreadPool()` 或评估 `newFixedThreadPool(n)` | 采纳 `newCachedThreadPool()` |
| `.sdkmanrc` | `17.0.12-tem` | `17.0.20-apt`（本机用 apt 安装的 17.0.20） |
| 其余项 | — | 与计划一致 |
