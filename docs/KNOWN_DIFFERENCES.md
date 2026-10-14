# 已知差异与边界

本文只列仍真实存在的差异。已修复的上游断口见 `MIGRATION_MAP.md`，不能把根因修复误记为功能缺失。

## 1. 框架实现不同，行为契约相同

- Java 使用 Spring AI Alibaba StateGraph；Python 使用显式 async 状态图引擎。16 节点、11 路由、
  39 状态键、重试、SSE 与 interrupt/resume 契约保持，但内部对象类型不可能二进制兼容。
- Java 用 Reactor `Flux`；Python 用 async generator。HTTP 层都输出 SSE，前端不感知内部差别。
- Java 用 MyBatis/JDBC；Python 管理库用 SQLAlchemy 2 async，分析数据库用 SQLAlchemy 或 JDBC
  bridge。事务边界按业务操作对齐，不复刻 Mapper 调用顺序。

## 2. 管理库初始化

Python 启动时用 SQLAlchemy `create_all` 幂等创建 15 张表；当前仓库没有 Alembic 版本迁移历史。
这适合新部署和本次重构验收。已有生产库以后若发生字段演进，应在上线前补 Alembic migration，
不能依赖 `create_all` 改列。

仓库保留上游 MySQL/PostgreSQL 示例分析数据，但不自动把上游 `data.sql` 的四个示例 Agent 和知识
条目注入每一个管理库，以免覆盖真实用户数据。示例数据源可在 UI 按 `STARTUP.md` 一次性添加。

## 3. Redis 是明确要求的 Python 增强

原 Java `main` 没有 Redis 运行时业务能力。Python 版按用户要求增加 Redis 7.4 热缓存，用于会话
列表/消息、多轮上下文和 HITL checkpoint；MySQL/SQLite 仍是事实源。正常并发下用同键分布式锁
消除“数据库已提交、旧缓存又被回填”的竞争；连接故障时当前进程绕过缓存并记录待失效键，恢复后
重放。若进程在 Redis 与管理库同时分区期间被强制终止，远端旧缓存只能由 TTL 清除，因此生产仍
应把 Redis 部署为高可用服务，不能把长 TTL 当作持久化保证。

## 4. 向量存储

- Simple 后端是 Python JSON 格式，不读取 Spring AI Java 对象序列化文件；迁移旧向量应重新建索引。
- JSON 后端已解决同一主机多进程写入丢失，但不承诺网络文件系统或多宿主分布式锁；多实例生产
  使用 Elasticsearch/Milvus。
- 候选 embedding space 成功切换后旧空间暂时保留，便于回滚。当前没有自动回收历史空间的 UI；
  需要运维确认无回滚需求后再按 `embeddingSpaceId` 清理。

## 5. Connector 环境差异

本机已真实验证 MySQL、PostgreSQL 和 SQLite。以下适配器有 URL、元数据、DDL、只读查询契约测试，
但最终连接仍依赖用户环境：

- H2、Dameng：需要 JRE、`jaydebeapi` 和对应 JDBC jar；
- SQL Server：需要 ODBC Driver 18 与 `pyodbc`；
- Oracle：需要 `oracledb` 及服务端；
- Hive：需要 `pyhive` 及其认证依赖。

这不是静默降级：缺驱动时连接测试明确失败并提示要安装的 extra/系统驱动。

## 6. 可选外部集成

以下能力实现和模拟/契约测试已存在，但本机没有可用服务或生产凭据完成全部 live 验证：

- Elasticsearch、Milvus 外部服务；
- 阿里云 OSS；
- Apache Tika 的所有旧格式；
- Langfuse OTLP 接收端；
- 企业 Oracle、Dameng、SQL Server、Hive、H2 jar 组合。

不能据此宣称这些外部系统已在用户生产环境验证；启动前应执行对应集成测试。

## 7. 模型与本地 BGE

- OpenAI-compatible Chat/Embedding 已做真实 HTTP smoke，但不同供应商的非标准字段仍可能需要设置
  自定义 `completionsPath` / `embeddingsPath`。
- 原 Java 仅按消息窗口裁掉旧轮次；Python 版额外统一限制总 Token，并对模型返回的上下文溢出做
  压缩重试。Token 计数是本地近似值，部署者仍须把 `DATAAGENT_CHAT_CONTEXT_WINDOW_TOKENS` 设为
  当前 CHAT 模型真实窗口。被裁掉的消息仍在数据库/UI 历史中，但当前不会自动生成跨轮语义摘要。
- BGE-M3 已在最终 Compose 后端容器中用 RTX 4060 / CUDA 12.4 真实产生 1024 维 dense 与
  sparse lexical 权重。交付源码包不携带 2GB+ 模型权重；换机器首次运行需下载，或指向完整本地
  模型目录。
- 历史验收环境已导入完整本地模型缓存，并在目标卷补全 snapshot 权重链接。删除命名卷后仍需
  重新下载或重新导入缓存；模型缓存不随源码发布。
- 历史 Compose 验收使用正式 `database` 模型模式，部署者自行配置并激活 CHAT；连接和真实
  NL2SQL 均通过，密钥只通过 API 掩码显示，本地 BGE-M3 承担 EMBEDDING。源码包不包含
  `.env` 或密钥，其他机器需重新配置 CHAT。

## 8. Sandbox 与 Docker

- 生成代码没有宿主机执行回退；Docker Engine 或镜像不可用时健康状态为 `degraded`。
- Compose 为实现任务级容器挂载 Docker socket，这具有高宿主权限。生产应使用隔离 Docker Host、
  固定镜像 digest、限制网络和镜像供应链。
- 历史 Windows Docker Desktop 验收曾遇到不可访问的 socket reparse point；保留恢复备份后
  Engine 已恢复，没有执行 factory reset。恢复备份不随源码发布；部署环境出现同类问题时需
  根据实际诊断处理，不能依赖源码包恢复本机 Docker 状态。

## 9. 报告资源与浏览器安全

HTML 报告包含完整页面结构和数据，但 Marked/ECharts 默认从配置的 HTTPS CDN 加载，因此不是严格
离线报告。无网络时 Markdown 原文仍可保留，但图表库不会加载。严格离线部署应把两个 JS 文件放入
可信静态目录并修改 `DATAAGENT_REPORT_MARKED_URL`、`DATAAGENT_REPORT_ECHARTS_URL`。

为了不复制上游风险，ECharts fenced block 只接受 JSON，不支持任意 JavaScript 表达式；报告还会
移除危险标签、事件属性和协议 URL。这可能让依赖原始 HTML/脚本的 Markdown 片段按文本显示，属于
有意安全收紧。

## 10. 鉴权边界

Agent 对外查询支持每 Agent API Key；管理端 API 与上游一样没有完整用户登录/RBAC 系统。生产部署
必须在反向代理或网关增加管理员认证、TLS、限流与审计，不能直接把 8065 暴露到公网。

## 11. 前端与许可证

前端以兼容修正为主，不是视觉重写；这减少 API 漂移并保留上游功能。项目沿用 Apache License 2.0，
来源基线和修改说明保留在审计及映射文档中。
