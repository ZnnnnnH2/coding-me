# 复杂后端支持升级清单 (Upgrade Plan for Complex Backend Support)

为了解决当前系统存在的“复杂规格被压扁为单实体 GET Demo”的问题，我们需要系统性地解除各层对“单主对象、只读列表（GET）、内存存储”的硬编码限制。

以下是按阶段划分的升级清单和优先级排期：

## 第一阶段：规格无损化与契约重建 (P0 - 基础阻断)

目标：让系统能够“听懂并记住”复杂的业务诉求，而不是在第一步就把输入压扁。

- [ ] **1.1 升级 Spec Parser (`src/codeingme/spec_parser/spec_bundle.py`)**
  - **OpenAPI 深度解析**：保留 Endpoint 的 `requestBody` 校验规则、多种 Response 状态码（201/400/404/409）及其数据结构。
  - **Schema 关联解析**：解析 SQL/DDL 中的外键约束，将实体关系转为领域模型图（Domain Model Graph），而不是孤立的表。
  - **业务规则可执行化**：解析 `business_rules.yaml` 并尝试将其转化为预期的“状态机转换契约”或“前置/后置条件”。
- [ ] **1.2 重构 Orchestrator 的 `RequirementSpec` (`src/codeingme/orchestrator/engine.py`)**
  - 废除通用的 3 条 `acceptance_criteria` 文本。
  - 引入多阶段验收目标（Multi-stage Acceptance Criteria）：基于 OpenAPI 的多种状态码（如测试通过必须涵盖 200, 400, 404, 409 的场景）。

## 第二阶段：解除 Architect 与 QA 的硬限制 (P0 - 核心卡点)

目标：让系统规划出的架构和测试用例能够涵盖多路由、多对象和写入操作。

- [ ] **2.1 重构 ArchitectAgent (`src/codeingme/agents/architect.py`)**
  - 移除 `GET {api_route}` 和 `id/title/completed` 的硬编码 Prompts 和输出期望约束。
  - 支持产出多实体契约（如 Header-Line 结构，或带有状态变更的审批流结构）。
  - 支持产出写操作路由契约（POST/PATCH/DELETE）。
- [ ] **2.2 重写 Generation Plan (`src/codeingme/generation_plan.py`)**
  - 将“单模块、单测试”模式修改为基于模块或领域的目录级生成（如：拆分 `models/`, `routers/`, `services/`）。
  - 支持多路由对应多份 API 合约测试。
- [ ] **2.3 升级 QAAgent (`src/codeingme/agents/qa.py`)**
  - 加入针对状态异常流转（409 Conflict）的断言生成。
  - 加入针对写入后查询一致性（POST 后 GET）的端到端覆盖测试。
  - 废弃仅检查 `completed` 和 `{"response_key": [...]}` 的列表检查。

## 第三阶段：Backend 实现的泛化 (P1 - 业务落地)

目标：让生成的后端代码脱离内存 Demo，具备处理并发、事务和真实业务流的能力。

- [ ] **3.1 改造 BackendAgent (`src/codeingme/agents/backend.py`)**
  - 移除基于 `list_...` 和单纯内存列表组装的快速起步代码约束。
  - 支持生成并注入标准的 Repository 模式（支持轻量级 ORM 如 SQLAlchemy 占位符或真实 SQLite 连接）。
  - 支持生成跨实体的业务逻辑（如操作 `ReturnRequest` 时更新 `ReturnRequestItem`，具备基础事务感）。
- [ ] **3.2 升级 DevOps 容器环境 (`src/codeingme/agents/devops.py`)**
  - 在运行时镜像中引入对数据库（例如持久化的 SQLite 文件）的支持。
  - 准备数据迁移（Migration）或 SQL 初始化脚本运行的自动化阶段。

## 第四阶段：复杂依赖图模型与级联修复 (P2 - 韧性与自愈)

目标：当复杂后端的某一个实体或接口生成出错时，能准确地找到影响面并精准修复。

- [ ] **4.1 重构图同步与依赖树 (`src/codeingme/orchestrator/engine.py` & `src/codeingme/graph/`)**
  - 修复引擎当前只抓取 `schemas[0]` 和 `apis[0]` 作为修复起点的逻辑。
  - 实现真正的代码与业务依赖树（例如：`ReturnRequest` 修改后，能自动标记并重新生成受影响的 `Receive Route` 与对应的单元测试）。
- [ ] **4.2 修复上下文的智能截取 (`src/codeingme/graph/slice_builder.py`)**
  - 针对多文件大型模块，修复时不要把所有无关实体也带入 Prompt。
  - 只截取图切片 (Graph Slice) 相关的上下文进行修复。
