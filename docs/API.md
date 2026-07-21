# HTTP API

服务默认监听 `http://127.0.0.1:8000`。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 运行模式、数据库、外部 API 与写作技能就绪状态 |
| GET | `/api/novels/{id}/research` | 已保存的网页研究来源与核验状态 |
| GET | `/api/novels/{id}/brief-versions` | 查看并对比创作方案历史版本 |
| POST | `/api/novels/{id}/semantic-search` | 在章节摘要和研究资料中执行语义检索；请求体包含 `query` 与可选 `limit` |
| PUT | `/api/novels/{id}/brief` | 在确认前手工修改完整创作方案并保存新版本 |
| POST | `/api/novels/{id}/brief-regenerate` | 根据 `feedback` 和 `sections` 整体或局部重做方案 |
| POST | `/api/novels/{id}/brief-restore` | 恢复指定历史版本，并把恢复结果保存为新版本 |
| DELETE | `/api/novels/{id}` | 删除尚未确认且没有章节的草稿项目 |
| GET | `/api/tags` | 男频/女频频道目录，以及各频道独立的题材、体验、元素、主角与感情线标签 |
| POST | `/api/novels` | 创建项目与创作方案 |
| GET | `/api/novels` | 项目列表 |
| GET | `/api/novels/{id}` | 项目、章节、人物、世界、时间线与已加载 Skill 聚合详情 |
| POST | `/api/novels/{id}/confirm` | 确认创作方案 |
| POST | `/api/novels/{id}/pause` | 暂停自动写作 |
| POST | `/api/novels/{id}/resume` | 恢复自动写作 |
| POST | `/api/novels/{id}/runs` | 运行 1 到 10 个连续章节工作流 |
| GET | `/api/novels/{id}/chapters` | 章节列表与正文 |
| GET | `/api/novels/{id}/characters` | 人物权威状态 |
| POST | `/api/novels/{id}/characters/rebuild` | 扫描全部 READY 章节，重建重要人物档案并清理非重要人物；在线模式会调用大模型 |
| GET | `/api/novels/{id}/world` | 世界硬规则与时间线 |
| GET | `/api/runs/{id}/events` | SSE 格式的工作流节点事件 |
| GET/PUT | `/api/settings` | API、写作技能就绪状态与非敏感本地偏好 |
| GET | `/api/publishing` | Mock 发布状态与安全提示 |

## 创建示例

```json
{
  "genre": "末世",
  "experiences": ["爽文", "热血"],
  "elements": ["囤货", "经营建设"],
  "protagonist_tags": ["重生", "普通人成长"],
  "idea": "仓储管理员依靠有限经验建立社区，没有无限空间。",
  "must_have": ["资源可追踪"],
  "nice_to_have": ["群像"],
  "exclude": ["过度血腥"],
  "chapter_target_chars": 2200,
  "daily_chapters": 2,
  "reserve_target": 5,
  "require_plan_confirmation": true
}
```

冲突标签返回 HTTP 409；非法结构返回 HTTP 422；尚未确认、已暂停或需人工处理的项目运行工作流时返回 HTTP 409。

`/api/health` 的 `apis.story_skills`、`apis.oh_story`，以及 `/api/settings` 的 `runtime.story_skills`、`runtime.oh_story` 会返回对应技能的固定来源、提交版本、已加载 Skill 名称、题材卡数量与启用状态。项目详情的 `skills` 会返回 `story_skills` 和 `oh_story`，当前项目匹配到的题材卡位于 `skills.oh_story.active_genre_card`。这些信息不包含 API Key 或完整正文。

章节规划数据除原有的目标、阻力、转折和钩子外，还包含全书阶段、剧情单元、读者目标情绪、本章爽点、新期待、主角目标和关键选择、多条剧情线推进、人物关系变化、信息差与场景情绪节拍。旧项目缺少这些字段时，运行时会根据现有方案补齐默认值。
