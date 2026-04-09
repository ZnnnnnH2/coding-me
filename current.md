# 复杂 Specs 在自动闭环中的压缩问题

复杂 specs 能写出来，但进入自动闭环后，会被逐层压扁。以下按链路详细拆开说明。

---

## 1. 规格解析层会丢掉大量复杂语义

`specs/` 里的 `openapi.yaml`、`schema.sql`、`business_rules.yaml` 可以写得很复杂，但当前 parser 提取的信息很少。

**关键位置**：`src/codeingme/spec_parser/spec_bundle.py:78`

**当前仅提取**：

- `service_name`
- `summary`
- `endpoints`
- `tables`
- `rules`

**问题**：

- OpenAPI 里的 `requestBody`、`responses`、参数定义、错误码、示例 payload 没有被结构化保留下来
- SQL 里的列级约束、外键关系、索引意义没有被转成 typed contract
- business rules 只是变成字符串列表，不是可执行的 workflow/state rules

**具体例子**（`specs/return_request_service/openapi.yaml`）：

- `POST /api/return-requests`
- `PATCH /review`
- `PATCH /receive`
- `PATCH /close`
- 多种 `409`/`404`/`400` 错误码
- 明细项结构
- 状态流转语义

**结果**：这些信息虽然写在 spec 里，但 parser 没有把它们转成后续 agent 真正理解的强结构。

---

## 2. 编排器并没有把"复杂规格"作为复杂问题来驱动

**关键位置**：`src/codeingme/orchestrator/engine.py:115`

orchestrator 最终构造的是一个很通用的 `RequirementSpec`：

- `title` = requirement_text
- `summary` = requirement_text
- `acceptance_criteria` = `["Generate contracts", "Drive red-to-green flow", "Propagate impacted changes"]`

**问题**：复杂 spec bundle 最终没有变成：

- 多阶段验收目标
- 多实体依赖图
- 状态机式 acceptance criteria
- 分操作 contract list

而是被收敛成一句 requirement 文本 + 三个通用目标。

**结果**：后续 agent 工作时，输入上下文已经比原始 specs 弱很多了。

---

## 3. ArchitectAgent 明确把问题限制成"一个主 schema + 一个主 GET API"

这是最直接的限制。

**关键位置**：`src/codeingme/agents/architect.py:63`

**硬编码要求**：

- one primary schema
- one primary API
- primary API method must be GET

**进一步限制**（`:67`、`:179`）：schema 必须包含：

- `id: int`
- `title: str`
- `completed: bool`

这已经不是"倾向于简单"，而是**硬编码成简单**。

**示例**：如果你的 complex spec 是：

- `return_requests`
- `return_request_items`
- `status`
- `approved_quantity`
- `received_quantity`
- `review_decision`

那 Architect 阶段不会把这些完整地建模出来，它会倾向于把问题压成：

- 一个主对象
- 一个 GET 路由
- 三个字段：`id` / `title` / `completed`

**结果**：复杂业务在第一层合同就已经缩水了。

---

## 4. BackendAgent 被硬限制成"内存列表 + 单列表接口"

这是第二个硬边界。

**关键位置**：`src/codeingme/agents/backend.py:42`

**硬编码要求**：

- 使用 FastAPI
- 使用 in-memory list-oriented storage
- service class 要有 `list_...` 方法
- 实现一个 `GET {api_route}`
- 返回 `{"response_key": [...]}`
- 包含两条内存记录
- 字段要有 `id`、`title`、`completed`

**默认实现**：`src/codeingme/agents/backend.py:125` 完全是这个形状。

**本质**：当前后端生成器并不是"泛化 backend generator"，而更像"生成一个领域化命名的列表型 FastAPI demo 模块"。

**天然不擅长的复杂后端要素**：

- 多个 write endpoint
- 跨实体聚合
- 事务
- 持久化数据库
- 外键一致性
- 审批流转
- 非法状态跳转拦截
- 幂等更新
- 领域错误模型
- 多资源联动

**具体例子**：当前 BackendAgent 对 `return_request_service` 很可能只会产出：

实际输出：

- `GET /api/return-requests`
- 内存里两条 return request
- 顶层 list payload

而不会自然产出：

- 创建申请
- 审核
- 收货
- 关闭
- header + items 聚合读模型
- 状态合法性判断

---

## 5. QAAgent 只验证"列表契约 + completed 可见性"

测试层也是同样的收缩。

**关键位置**：`src/codeingme/agents/qa.py:41-49`

**硬编码要求**：

- 加一个 contract test for GET
- 加一个 business-rule test，证明 `completed` / open items 都可见
- 顶层是一个 list response
- 字段是 `id` / `title` / `completed`

**默认测试源码**（`:161`）只检查：

- HTTP 200
- payload 里有列表
- 第一项有 `id` / `title` / `completed`
- 列表里既有 `completed=True` 也有 `completed=False`

**问题**：当前 QA 并不会自动覆盖复杂 specs 常见的关键点：

- POST 创建输入校验
- 404 未知对象
- 409 非法状态流转
- detail endpoint 聚合返回
- 明细数量一致性
- `approved → received → closed` 的合法路径
- 拒绝态终止

**本质**：不是模型"没想到"，而是测试 harness 根本没有把这些东西当成主验证目标。

---

## 6. 生成计划默认就是"一个主 schema → 一个模块 → 一个测试文件"

**关键位置**：`src/codeingme/generation_plan.py:22`

**生成计划仅围绕**：

- 一个 `schema_name`
- 一个 `response_key`
- 一个 `backend_module_path`
- 一个 `test_module_path`

**关键取值**（`:39`）：schema 直接取 `context.schemas[0]`

**问题**：即便前面某一步真的给了多个 schema，当前命名和产物规划仍然是"单主对象思维"。

**复杂后端通常需要**：

- 多个资源
- 多个模块
- 多个 contract test 文件
- 多个 service/repository 边界

**结果**：这里的文件规划不是为这种情况设计的。

---

## 7. 图模型和 cascade repair 也主要围绕"第一个 schema / 第一个 api"

**关键位置**：

- `src/codeingme/orchestrator/engine.py:161` - 变更起点

  ```python
  changed_node_id = self._schema_node_id(schemas[0])
  ```

- `src/codeingme/orchestrator/engine.py:534-535` - runtime node sync
  ```python
  schema = context.schemas[0]
  api = context.apis[0]
  ```

**问题**：repair / graph sync 的核心路径也是单主 schema、单主 api 的模型。

**复杂后端带来的问题**：

- 多实体依赖关系不会被完整建图
- 局部修复时，blast radius 容易不准

**具体例子**：`return_request_service` 里的关系：

- `return_requests`
- `return_request_items`
- review route
- receive route
- close route

这些之间的关系不是"一个 schema + 一个 api"能完整表达的。

---

## 8. DevOps 层也还是 demo 级，而不是真实复杂后端运行级

**关键位置**：`src/codeingme/agents/devops.py:41-45`

**容器约束**：

- `python:3.11-slim`
- 安装 `fastapi httpx pytest uvicorn`
- copy demo_app
- copy tests_generated
- 用 uvicorn 起 app
- 用 pytest 跑 tests

**适用场景**：demo

**不适用于复杂后端真实落地场景**：

- 数据库服务
- migration
- 持久化存储
- 异步 worker
- 消息队列
- 缓存
- 外部依赖 mock
- 多服务 compose

**结果**：即使前面真生成了复杂业务代码，当前运行/验证环境也未必能承接。

---

## 9. 默认 acceptance tests 本身也只有两类

**关键位置**：`src/codeingme/contracts/test_generation.py:8`

**默认验收测试**：

- contract test
- rule visibility test

**问题**：系统现在的"红绿循环骨架"本来就是围绕一个小型 demo contract 设计的，不是围绕复杂业务测试矩阵设计的。

---

## 10. 单元测试和 e2e 测试也在加固这种简化假设

**关键位置**：`tests/unit/test_llm.py`

**测试断言期望** agent 生成：

- `GET /api/tasks`
- `{"tasks": [...]}`
- `id` / `title` / `completed`
- `tests_generated/test_tasks_demo.py`

**问题**：repo 当前测试基线本身就在"奖励简单列表接口"，而不是奖励复杂后端。

---

## 核心结论

> **当前闭环不是"不能写复杂 specs"，而是"它的 parser、contracts、qa、backend、graph、runtime、tests 全部都在共同假设：目标是一个小型、单主对象、列表导向的 FastAPI demo 模块"。**

这就是为什么复杂 specs 进入闭环后，通常会出现三种结果：

- 被忽略一部分复杂语义
- 被压缩成一个更简单的读模型
- 表面跑通，但没有真正实现复杂规则

---

## 下一步建议

可以继续做一件更有用的事：

**把"当前闭环距离支持复杂后端，还差哪些能力"列成一张升级清单，按优先级排出来。**
