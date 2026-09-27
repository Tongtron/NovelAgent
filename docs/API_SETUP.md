# 需要用户补充的 API 清单

离线版已经能跑通全部状态与数据闭环，但离线生成器只用于工程验证。以下能力需要你后续选择供应商并补充信息。

## 1. DeepSeek 大模型 API（已实现，真实创作必需）

需要补充：

- `LLM_PROVIDER=deepseek`
- `LLM_API_KEY`：密钥，写入本机 `.env.local`。
- `LLM_BASE_URL=https://api.deepseek.com`
- `LLM_MODEL=deepseek-v4-pro`

代码接入点：`novel_agent/adapters_remote.py` 的 `DeepSeekLLMGateway`。创作方案、情绪优先的细化章节计划、题材化正文生成、去 AI 味处理、Story Skills 一致性审校和修订已经实现；结构化结果使用 JSON Output，并由 `novel_agent/models.py` 中的 Pydantic 模型再次校验。密钥缺失、鉴权失败、限流、超时、服务端错误、空响应和截断输出都有明确错误类型。

`STORY_SKILLS_ENABLED=true` 默认启用固定的 `revision-continuity` 审校。在线模式下，初稿和每次自动修订后都会增加一次 DeepSeek 请求；设置为 `false` 并重启可以临时关闭。Story Skills 本身不需要额外 API Key。

`OH_STORY_ENABLED=true` 默认启用固定版本的 oh-story `story-long-write` 与 `story-deslop`。系统会加载题材正文提示卡，并在每章初稿后增加一次 DeepSeek 去 AI 味请求；设置为 `false` 并重启可以临时关闭。oh-story 本身不需要额外 API Key，但在线模式的调用次数和费用会略有增加。

## 2. Tavily 搜索与网页阅读 API（已实现，联网研究需要）

需要补充：

- `SEARCH_PROVIDER=tavily`
- `SEARCH_API_KEY`：Tavily 密钥
- `SEARCH_BASE_URL=https://api.tavily.com`
- `SEARCH_MAX_RESULTS=3`：每个问题最多保存 1-5 个来源

代码接入点：`TavilySearchAdapter`。它会执行搜索并读取清洗后的网页正文；结果保存查询、标题、URL、域名、可信度、正文摘录、发布时间和核验状态。同一问题来自至少两个独立域名时标记为可交叉核验；单一来源会明确要求正文保守使用。

## 3. SiliconFlow Embedding API（已实现，语义记忆可选）

需要补充：

- `EMBEDDING_PROVIDER=siliconflow`
- `EMBEDDING_API_KEY`：SiliconFlow 密钥（不能使用 DeepSeek 密钥）
- `EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1`
- `EMBEDDING_MODEL=BAAI/bge-m3`
- `SEMANTIC_RETRIEVAL_LIMIT=6`

它只用于章节摘要和研究资料的派生检索。每次生成章节前，系统会自动回填尚未建立索引的历史摘要与来源，再以余弦相似度召回相关内容。人物当前状态、世界硬规则和时间线继续保存在权威关系表中，不会迁移为仅向量存储。

## 4. 番茄发布能力（当前禁止真实调用）

需要在实施前先确认可用且被允许的稳定接入方式，再补充：

- 发布接口地址或获准的集成方式。
- 鉴权令牌/凭证。
- 平台作品 ID 映射。
- 发布频率、格式、幂等键、验证码和人工确认策略。

代码接入点：`FanqiePublisherAdapter`。当前 `MockPublisher` 只生成本地模拟结果，真实适配器构造时会直接中止，防止误发。

## 5. 生产基础设施（长期无人值守时需要）

- `DATABASE_URL`：PostgreSQL/pgvector。
- `REDIS_URL`：缓存、锁与 SSE 事件转发。
- `S3_ENDPOINT`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_BUCKET`：正文版本、导出与资料快照。
- Temporal 服务地址与命名空间：替换本地一次性调度器。

`docker-compose.yml` 提供了 PostgreSQL、Redis 和 MinIO 的可选 profile，但示例密码必须修改后才能使用。

## 安全启用顺序

1. 先选择一个 LLM 供应商，只实现模型网关并跑现有回归测试。
2. 使用非敏感示例项目验证结构化输出、情绪规划、题材卡、去 AI 味、Story Skills 复审、重试、成本与超时。
3. 再接搜索，并检查来源追溯和搜索故障降级。
4. 配置 Embedding，确认历史摘要自动回填和语义召回。
5. 再迁移 PostgreSQL/Temporal，进行 30 章连续测试。
6. 真实发布最后单独实施，并保留人工闸门和章节储备。

在这些步骤完成前，保持 `NOVEL_AGENT_MODE=offline`。

项目只读取已审查并固定版本的 Markdown 技能内容和题材卡，不执行 oh-story 上游仓库附带的安装脚本、Hooks、自定义 Agent 或 Node.js 脚本。SQLite 仍是唯一业务数据源。
