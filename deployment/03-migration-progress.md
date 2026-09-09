# 服务器搬运进度说明

## 当前目标

将 `Memind-Local-Dev` 项目搬运到 Ubuntu 服务器 `192.168.100.230` 的 `/data/lijing/Memory/Memind-Local-Dev` 目录下，并在服务器上完成编译、启动与基础验证，为后续问题排查建立独立环境。

## 已完成事项

### 1. 项目已同步到服务器

本地项目已通过 `rsync` 同步到服务器目标目录：

```text
/data/lijing/Memory/Memind-Local-Dev
```

同步时已包含当前项目代码与部署文档目录。

### 2. 部署文档已放入项目中

项目内已新增部署相关文档目录：

```text
/home/zzx/py/Memind-Local-Dev/deployment/
```

目前包含：

- `01-deployment-checklist.md`
- `02-collaboration-debug-checklist.md`
- `03-migration-progress.md`

### 3. 服务器基础环境已确认

服务器上已确认：

- Java 21 已安装
- 项目目录已存在
- SSH 可连接
- 目标路径可访问

## 当前进展

### 1. Maven 初始版本过旧

服务器默认 `mvn -v` 显示的是：

```text
Apache Maven 3.6.3
```

这与 Java 21 存在兼容性问题，导致编译时出现：

- `InaccessibleObjectException`
- `com.google.inject.internal.cglib.core.$MethodWrapper` 初始化失败

### 2. 已确认需要升级 Maven

当前需要改用更高版本的 Maven，例如 `3.9.9`，并确保终端实际调用的是新版本，而不是系统自带的旧版本。

### 3. 项目编译目前仍未完成

此前使用以下命令时出现两类问题：

- 旧 Maven 与 Java 21 兼容性问题
- `memind-server` 编译时本地 SNAPSHOT 模块未自动带上，需要使用 `-am`
- 后续又遇到 license header 检查失败

## 已发现的问题

### 1. Maven 版本与 Java 21 不兼容

系统默认 Maven 版本过旧，需要切换到新版本 Maven。

### 2. 多模块编译方式需要调整

单独编译 `memind-server` 时，本地依赖模块会缺失，需要使用：

```bash
mvn -pl memind-server -am -DskipTests compile
```

### 3. 许可证头检查会阻断构建

构建过程中，`license-maven-plugin` 报告部分文件缺少标准 License Header，导致 `BUILD FAILURE`。

## 下一步建议

### 1. 先切换到新 Maven

确保当前 shell 实际使用的是新 Maven，而不是 `/usr/share/maven` 的旧版本。

### 2. 再执行多模块编译

使用带 `-am` 的命令编译主模块及其本地依赖：

```bash
mvn -pl memind-server -am -DskipTests compile
```

### 3. 如仍失败，处理 license header

如果构建继续被 `license-maven-plugin` 拦截，需要进一步定位缺少头部的文件并补齐标准 Apache 2.0 License Header。

### 4. 编译成功后再启动服务

```bash
mvn -pl memind-server spring-boot:run
```

### 5. 验证端口与接口

- 检查 `8366` 是否监听
- 调用 `retrieve` 接口确认服务可用

## 当前结论

项目搬运已完成，服务器目录已建立，基础运行环境中的 Java 部分已具备；当前卡点在于：

1. Maven 版本需要切换到新版
2. 多模块编译方式需要使用 `-am`
3. 构建还需要处理 license header 检查

只要这三项处理完成，后续就可以进入服务器上的编译、启动和问题排查阶段。
