# Memind 本地启动命令

项目目录：`/home/zzx/py/Memind-jdk17`（JDK 17 迁移版）

> JDK 21→17 迁移详情见：`JDK17_MIGRATION_COMPLETED.md`
> 本文档的命令均已实际验证可跑通，按顺序执行即可。

端口约定：

- 后端：默认 `8366`（与原版项目一致，`application.yml` 中 `${SERVER_PORT:8366}`）
- 前端（Vite dev）：`5173`，代理指向 `http://localhost:8366`
- **不需要在终端覆盖端口**，直接启动即可
- 仅当本机 8366 已被占用（比如原版项目同时在跑、需要两个实例并存）时，
  才临时覆盖为 18366（在 8366 前面加个 1），见「端口冲突时的临时覆盖」

## 第 0 步（仅新拷贝目录/换环境时需要）：一次性准备

以下两条必须在第一次启动前执行，否则会复现
「类文件具有错误的版本 65.0, 应为 61.0」（旧 JDK 21 快照缓存）
或「vite: not found」（前端依赖未装）的错误。

### 0.1 清掉旧 JDK 21 编译的本地仓库缓存，用 JDK 17 重装

```bash
rm -rf ~/.m2/repository/com/openmemind/ai
cd /home/zzx/py/Memind-jdk17
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
mvn clean install -DskipTests -Dlicense.skip=true -Dcheckstyle.skip=true
```

看到 `BUILD SUCCESS` 即完成。

### 0.2 安装前端依赖

```bash
cd /home/zzx/py/Memind-jdk17/memind-ui && pnpm install
```

> 之后每次改动 `memind-core` / `memind-plugins` 的代码，需重新执行 0.1，
> 或在启动后端时用带 `-am` 的写法（见下文「保险写法」），让 Maven 现编依赖。

## JDK 准备（JDK 17）

本项目已迁移到 JDK 17。本机已通过 apt 安装 OpenJDK 17.0.20：

```bash
/usr/lib/jvm/java-17-openjdk-amd64/bin/java -version
```

由于 `~/.sdkman/candidates/java` 目录归 root 所有（无法写入），无法把 JDK 17
软链进 SDKMAN，`.sdkmanrc` 中的 `java=17.0.20-apt` 暂时只能作为记录。
启动后端时**不要**用 `sdk env`，改用 `JAVA_HOME` 直接指向 apt 的 JDK 17。

## 准备 .env

首次启动前，从模板创建 `.env` 并填入模型 key 等配置：

```bash
cd /home/zzx/py/Memind-jdk17
cp .env.example .env
# 编辑 .env，填入 API key 等配置
```

注意：`.env` 里写不写 `SERVER_PORT` 都可以。不写时后端使用默认端口 8366；
若写了其他端口（如 `SERVER_PORT=18366`），以 `.env` 为准。当前 `.env` 未配置端口，
后端启动即为 8366。

## 启动顺序

按以下顺序启动：

1. 启动后端
2. 等待后端启动成功
3. 启动前端
4. 打开浏览器

## 终端一：启动后端

```bash
cd /home/zzx/py/Memind-jdk17
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
set -a
source .env
set +a
mvn -pl memind-server spring-boot:run
```

保险写法（`-am` 会在 reactor 内现编 memind-core 等依赖，**不会踩到本地仓库
旧快照缓存的坑**，改完后端代码后推荐用这条）：

```bash
mvn -pl memind-server -am spring-boot:run
```

看到以下日志说明后端启动成功：

```text
Tomcat started on port 8366
Started MemindServerApplication
```

后端地址：`http://localhost:8366`

健康检查：

```bash
curl -sS http://localhost:8366/open/v1/health
```

说明：

- `.env` 没有修改时，后端运行期间不需要重启。
- 修改 `.env`、Spring 配置或后端代码后，根据修改内容重启后端。
- `set -a`、`source .env`、`set +a` 用于将 `.env` 配置导出给 Maven 和 Java 进程。
- 同一终端重启后端：先 `Ctrl+C`，再执行
  `set -a && source .env && set +a && mvn -pl memind-server spring-boot:run`。

### 端口冲突时的临时覆盖（仅在需要时）

如果 8366 被占用（例如原版项目同时在跑），可临时用 18366 启动本副本：

```bash
cd /home/zzx/py/Memind-jdk17
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
set -a
source .env
set +a
export SERVER_PORT=18366
mvn -pl memind-server spring-boot:run
```

要点：`export SERVER_PORT=18366` 必须放在 `source .env` **之后**（顺序反了会被
`.env` 覆盖回 8366）；此时前端代理仍指向 8366，需要同时改
`memind-ui/vite.config.ts` 的 `target` 后再起前端。健康检查也相应换成 18366。
测试完成记得还原，日常启动不要带这一行。

## 终端二：启动前端

后端启动成功后，再打开第二个终端：

```bash
cd /home/zzx/py/Memind-jdk17/memind-ui
pnpm install
pnpm dev
```

看到 Vite 的 `Local` 地址后，访问：

```text
http://localhost:5173
```

前端对 `/admin` 的代理指向 `http://localhost:8366`。

## 停止顺序

1. 在前端终端按 `Ctrl+C`
2. 在后端终端按 `Ctrl+C`

## Java 测试工具

测试脚本统一存放在：

```text
/home/zzx/py/Memind-jdk17/dev-tools/java
```

运行模型接口测试：

```bash
cd /home/zzx/py/Memind-jdk17
set -a
source .env
set +a
java dev-tools/java/ModelApiSmokeTest.java all
```

运行 Memind 后端完整测试：

```bash
cd /home/zzx/py/Memind-jdk17
java dev-tools/java/MemindApiSmokeTest.java all
```

可分别运行：

```bash
java dev-tools/java/MemindApiSmokeTest.java health
java dev-tools/java/MemindApiSmokeTest.java extract
java dev-tools/java/MemindApiSmokeTest.java retrieve
java dev-tools/java/MemindApiSmokeTest.java dashboard
```

最小闭环验证（写入一条自定义记忆 → 查询它，已验证 PASS）：

```bash
cd /home/zzx/py/Memind-jdk17 && export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 MEMIND_TEST_MEMORY='我家里有一只猫叫团团。' MEMIND_TEST_QUERY='用户家里养的猫叫什么名字？' && java dev-tools/java/MemindApiSmokeTest.java extract && java dev-tools/java/MemindApiSmokeTest.java retrieve
```

（若后端临时起在 18366，给上述命令加上环境变量 `MEMIND_BASE_URL=http://localhost:18366` 即可。）

## 常见错误速查

| 报错 | 原因 | 解决 |
|------|------|------|
| `类文件具有错误的版本 65.0, 应为 61.0` | 本地仓库缓存了 JDK 21 编译的旧 memind-core 快照 | 执行第 0.1 步清缓存重装；日常改代码后用带 `-am` 的启动命令 |
| `sh: 1: vite: not found` | 前端依赖未安装 | `cd memind-ui && pnpm install` |
| 前端页面请求后端 404/连接失败 | 后端临时起在 18366，但前端代理仍指向 8366（或反之） | 两边对齐：改 `memind-ui/vite.config.ts` 的 `target`，或后端改回默认 8366 |
