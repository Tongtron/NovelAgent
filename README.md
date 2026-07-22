# Novel Agent

个人使用的自主长篇小说作者智能体。它根据标签和核心想法建立创作方案，再以独立章节工作流持续执行规划、研究判断、写作、审核、修订、记忆更新和章节储备。

当前版本支持离线模式和 DeepSeek 在线模式。仓库的示例配置仍以离线模式作为安全初始值；本机项目已在 `.env.local` 中启用 DeepSeek 在线模式，因此生成创作方案、情绪细纲、正文、oh-story 去 AI 味、Story Skills 一致性审校、审核修订和重要人物档案时会调用 DeepSeek API，并发送完成该次创作所需的上下文。普通浏览页面和阅读已有正文不会调用模型。

完整的安装、配置和日常操作说明见 [Novel Agent 使用手册](docs/USER_GUIDE.md)。

## 已实现

- 男频/女频读者频道，以及随频道联动的题材、阅读体验、核心元素、主角设定和感情线标签。
- 标签、多级偏好、必须包含和排除项；显式冲突会中止创建。
- 创作方案、主角、世界硬规则、开篇三章与更新策略。
- 每本书独立的重要人物档案库；只跟踪会持续影响主线、关键资源信息、核心冲突或长期关系的人物，并更新其目标、位置、知识与关系。
- 一次性具名路人和临时工具人不建档；旧作品可从“长期记忆”扫描 READY 章节并清理非重要档案。
- 人物库采用目录/详情分离：电脑端左侧选择人物、右侧查看单人档案，手机端在人物列表与详情之间切换。
- 方案确认、暂停、继续和异常状态。
- 用户指定书名、方案手工编辑、反馈/局部重做、版本对比恢复，以及带书名确认的作品删除。
- 已完成章节可带修改意见打回重做；后续章节和衍生记忆会在同一事务中回滚。
- 本地 Token 用量按日统计，控制台图表不依赖外部 CDN。
- 单章低 Token 工作流与运行事件：题材卡 → 单次精简细纲 → 调研判断 → 上一章正文接续写作 → 去 AI 味 → 八维可读性审稿 → Story Skills 一致性审校 → 修订 → 记忆提交 → READY。
- 每章生成一份包含目标情绪、冲突、关键选择、可见代价、转折和情节点预算的细纲；重试时沿用原细纲和审稿意见，避免重复规划。
- 第二章起向正文作者提供上一章末尾摘录；成稿按开篇钩子、场景因果、冲突升级、人物主动性、对话辨识度、具体呈现、读者回报和章末钩子执行可读性门禁。
- 固定引入 `worldwonderer/oh-story-claudecode` 的 `story-long-write` 与 `story-deslop`：先确定读者情绪和回报，再生成带阶段、剧情单元、关键选择、多线推进、关系变化和字数预算的章节细纲；正文按唯一题材卡校准并在写后最小化去 AI 味。
- 固定引入 `danjdewhurst/story-skills` 的 `revision-continuity` 与 `story-maintenance`；SQLite 保持唯一权威数据源，本地规则与 DeepSeek 语义审校共同阻止跨章节矛盾。
- 草稿/正式版本隔离；正式正文、人物状态和时间线在同一事务内提交。
- 普通质量未达标只保留 FAILED 草稿且项目可继续重试；只有明确的人工阻断问题才进入 HUMAN_REQUIRED。
- SQLite 权威存储、章节储备、本地调度器和失败检查点。
- 自主作者控制台、作品阅读、人物/世界/时间线管理视图。
- SSE 历史事件接口、MockPublisher、安全 API 配置页。
- DeepSeek V4 网关、JSON Output、结构校验、超时、重试和错误分类。
- Tavily 搜索、网页正文读取、来源可信度评分与独立域名交叉核验。
- SiliconFlow/OpenAI-compatible Embedding、章节摘要与研究资料语义召回。
- 领域与状态回归测试。

## 立即运行

要求：Python 3.11 或更高版本，并安装 `pydantic>=2.8`。

```powershell
cd D:\NovelAgent
python -m novel_agent serve
```

然后访问 `http://127.0.0.1:8000`。也可以双击 `run.cmd`，或在 PowerShell 执行：

```powershell
.\run.ps1
```

联网调研与语义检索默认关闭。启用方式见 `docs/API_SETUP.md`；搜索和 Embedding
失败时会降级为保守写法与最近章节上下文，不会阻断正文生产。

离线命令行演示：

```powershell
python -m novel_agent demo --chapters 2
python -m novel_agent list
python -m novel_agent tick --limit 1
```

运行测试：

```powershell
python -m unittest discover -s tests -v
```

## 目录

```text
apps/
  api/                 HTTP 服务入口
  web/                 零外部依赖的自主作者控制台
  agent_worker/        独立章节任务入口
  scheduler_worker/    本地储备补充入口
novel_agent/
  adapters.py          LLM、搜索与发布抽象；离线/Mock 实现
  adapters_remote.py   真实 API 的受控接入点
  api.py               REST、SSE 与静态站点服务
  models.py            Pydantic 领域与工作流模型
  repository.py        SQLite 权威数据与原子提交
  requirements.py      标签与约束冲突检查
  story_skills.py      第三方 Story Skills 注册、上下文与一致性门禁
  writing_skills.py    oh-story 题材卡、情绪细纲与去 AI 味门禁
  workflow.py          单章状态机、审核与修订循环
  scheduler.py         本地渐进式调度边界
vendor/story-skills/   固定版本的第三方 Skill、来源与 MIT 许可
vendor/oh-story/       固定版本的 oh-story 写作 Skill、题材卡与 MIT 许可
tests/                  状态、事务与工作流测试
docs/                   架构、接口和 API 补充说明
```

## 数据与安全

- 默认数据库是 `data/novel_agent.db`，已被 `.gitignore` 排除。
- `.env` 与 `.env.local` 已被排除，真实密钥不得写入 `.env.example` 或源码。
- `NOVEL_AGENT_MODE=offline` 时所有外部模型和搜索调用都关闭。
- `STORY_SKILLS_ENABLED=true` 默认启用本地一致性门禁；在线模式下每轮审校会增加一次 LLM 请求。
- `OH_STORY_ENABLED=true` 默认启用情绪细纲、题材卡和去 AI 味；在线模式下每个初稿增加一次 LLM 润色请求。
- 第三方 Story Skills 的 Node CLI 不会自动执行，SQLite 是唯一权威故事存储。
- 真实平台发布没有实现；系统只返回 Mock 发布状态。
- 完整正文不放入工作流状态；最近摘要和结构化事实用于上下文。

API 接入位置见 [docs/API_SETUP.md](docs/API_SETUP.md)，架构与状态边界见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，HTTP 接口见 [docs/API.md](docs/API.md)。

## 文档维护约定

以后新增或修改用户可见功能、操作流程、环境变量、接口、数据路径和故障处理方式时，必须在同一次更新中同步修改 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

## 启用 DeepSeek

在不会提交到版本库的 `.env.local` 中填写：

```env
NOVEL_AGENT_MODE=online
LLM_PROVIDER=deepseek
LLM_API_KEY=你的DeepSeek密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
STORY_SKILLS_ENABLED=true
OH_STORY_ENABLED=true
```

也可以使用 `DEEPSEEK_API_KEY` 代替 `LLM_API_KEY`。密钥缺失时在线模式会拒绝启动，不会静默退回离线生成器。

## 当前实现与目标架构的关系

当前运行时用标准库 HTTP 服务和 SQLite 保证无需下载依赖即可启动。模块边界对应策划书中的 FastAPI、LangGraph、PostgreSQL、Temporal、Redis 和对象存储层；它们是生产演进目标，不是当前离线运行的前置条件。生产依赖已放入 `pyproject.toml` 的 `production` 可选组，基础设施样例位于 `docker-compose.yml`。
