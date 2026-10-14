# API、SSE 与前端兼容性

## 1. 覆盖结论

OpenAPI 契约测试固定了 101 个 HTTP 操作：

- 98 个 Spring AI Alibaba DataAgent 上游映射；
- 2 个 Nuxt 已调用、但 Java 后端缺失的兼容端点；
- 1 个增强健康端点 `/health`。

两个兼容端点为：

- `GET /api/agent-knowledge/agent/{agentId}`
- `GET /api/agent-knowledge/statistics/{agentId}`

原路径、HTTP 方法、camelCase 字段、分页外形、模型密钥掩码和主要成功/失败消息均保留。完整
上游 98 项列表见 `ORIGINAL_ARCHITECTURE_AUDIT.md`，机器可校验集合见
`tests/test_api_contract.py`。

## 2. 响应约定

上游存在两种返回形态，Python 版按端点保留：

1. 直接实体/数组，例如 `GET /api/agent/list`、`GET /api/datasource`；
2. 包装响应：

```json
{
  "success": true,
  "message": "success",
  "data": {}
}
```

分页额外包含 `total`、`pageNum`、`pageSize`、`totalPages`。领域输入错误和 Pydantic 校验错误
统一返回可读的 400 包装响应；不存在返回 404；资源冲突返回 409；外部模型/配置不可用按语义
返回 502/503。

增强健康端点额外返回 `redis.enabled/required/ready`、命中/未命中/错误和待失效数。Redis 配置后
不可达时总体状态为 `degraded`，但管理库可用时聊天和 Agent memory 读取仍会回退数据库。

## 3. Graph SSE

入口保持：

```text
GET /api/stream/search
```

查询参数：`agentId`、`conversationId`、`threadId`、`query`、`humanFeedback`、
`humanFeedbackContent`、`rejectedPlan`、`nl2sqlOnly`。

认证头：`X-API-Key` 或 `Authorization: Bearer ...`。只有 Agent 启用 API Key 时才强制认证。

每个数据帧为：

```text
event: complete|error        # 普通节点帧可没有 event 行
data: {GraphNodeResponse JSON}

```

`GraphNodeResponse` 保留 `agentId`、`threadId`、`nodeName`、`nodeTitle`、`stepId`、`attempt`、
`eventType`、`textType`、`text`、`error` 等前端时间线字段。正常完成最后发送 `complete`；图内异常
转换为 `error` 帧，不把 Python traceback 暴露给浏览器。

停止接口保持：

```text
POST /api/stream/stop?conversationId=...&threadId=...
```

它同时设置运行中取消标志并删除对应 checkpoint；已停止的 HITL 任务不能被旧 `threadId` 复活。

## 4. Human-in-the-loop

开启审核后，首次流在 HumanFeedback 前结束并返回：

- `eventType = HUMAN_FEEDBACK_REQUIRED`
- 当前 `threadId`
- 待审核 Planner JSON

恢复请求必须复用 `threadId`。`humanFeedbackContent=Accept` 继续执行；拒绝会清除旧 Planner 输出并
把反馈送回 Planner。错误 Agent、错误 conversation 或过期 checkpoint 均拒绝恢复，防止串会话。

## 5. 前端兼容修正

保留上游 Nuxt 页面、组件、Pinia store 与视觉结构，修改范围为：

- `EventSource` 改为 fetch-based SSE，支持 API Key Header 与错误体；
- Agent 表单保存 `humanReviewEnabled`，运行页仍可按本轮覆盖；
- 模型编辑时掩码密钥不会覆盖数据库中的真实值；
- 数据源表选择、搜索与逻辑外键请求契约对齐；
- 报告兼容 Marked 12 renderer 参数，并严格 JSON 解析 ECharts 配置；
- 报告下载过滤危险标签、事件属性、URL，不执行 LLM 生成代码。

前端通过 `NUXT_BACKEND_URL` 在服务端代理 `/api`；浏览器默认只访问同源 Nuxt 地址。开发模式
可按 `frontend/nuxt.config.ts` 配置后端地址。

## 6. 会话与标题 SSE

会话 CRUD、消息保存、置顶、重命名、软删除与 HTML 报告下载保持原路径。首次消息可异步生成
标题，并从下列端点推送：

```text
GET /api/agent/{agentId}/sessions/stream
event: title-updated
data: {"agentId": ..., "sessionId": ..., "title": ...}
```

内存发布器采用有界订阅队列和 keep-alive。它用于 UI 即时刷新；标题本身已先写管理库，因此
客户端断线重连后不会丢最终状态。

## 7. MCP 接口

默认地址：`http://127.0.0.1:8066/mcp`，协议为 MCP Streamable HTTP，stateless JSON response。
也支持：

- `DATAAGENT_MCP_TRANSPORT=sse`：旧 SSE transport；
- `DATAAGENT_MCP_TRANSPORT=stdio`：本地 MCP client。

Tool 名保持上游：

- `listAgentsToolCallback(status?, keyword?)`
- `nl2SqlToolCallback(naturalQuery, agentId)`

实际 MCP SDK initialize、list_tools、call_tool 已纳入 `tools/mcp_smoke.py`。

## 8. OpenAPI 与自动核对

FastAPI 文档：`http://127.0.0.1:8065/docs`，Schema：`/openapi.json`。

```powershell
python -m pytest tests/test_api_contract.py
```

该测试要求实际操作集合与冻结清单完全相等，新增、删除或改路径都会失败，而不只是检查一个总数。
