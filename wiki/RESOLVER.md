# RESOLVER — Wiki 页面分类路由

> 当 agent 需要创建新 wiki 页面时，按以下规则决定放入哪个目录。

## 路由规则

| 条件 | 目录 | type | 示例 |
|------|------|------|------|
| 人物（同事、合作伙伴、行业人物、公众人物） | `people/` | person | Alice、Karpathy |
| 项目（工作项目、个人项目、开源项目、有明确目标和时间线的） | `projects/` | project | 百家号AI、Wiki Brain |
| 产品/组织/公司/平台（无明确项目边界的实体） | `entities/` | entity | OpenViking、太极AI、Synology |
| 心智模型/框架/技术概念/方法论 | `concepts/` | concept | GBrain、LLM Wiki、Transformer |
| 会议记录/讨论纪要/决策记录 | `meetings/` | meeting | 周会、需求评审、技术方案讨论 |
| 创意/想法/灵感/待探索方向 | `ideas/` | idea | 产品方向、技术预研、副业构想 |
| 对比分析 | `comparisons/` | comparison | Hermes vs OpenClaw |
| 查询记录和回答 | `queries/` | query | 如何配置XXX |

## 模糊情况处理

- **项目 vs 产品**：有明确起止时间和目标 → `projects/`；持续运营的实体 → `entities/`
- **人物 vs 实体**：具体的人 → `people/`；泛指的组织/公司 → `entities/`
- **会议 vs 概念**：具体某次会议记录 → `meetings/`；从会议中提炼的方法论 → `concepts/`
- **想法 vs 项目**：初步构想、未立项 → `ideas/`；已开始执行 → `projects/`

## Agent 行为

1. 创建新页面前，先检查 RESOLVER 确定目录
2. 如果不确定，默认放入 `concepts/` 并标记 `status: draft`
3. Dream Cycle 审计时可以纠正分类错误

## 源材料目录

> `src/` 存放外部资源的原始材料，**不参与** auto_index 和 OpenViking 同步，仅供 agent 引用。

| 子目录 | 用途 | 说明 |
|--------|------|------|
| `src/agency-agents/` | Agent 角色模板库 | 185 个 Agent .md 文件（github.com/msitarzewski/agency-agents），按部门分子目录 |
| `src/articles/` | 文章/网页存档 | 收藏的网页文章、博客、教程等文本材料 |
| `src/audio/` | 音频文件 | 播客录音、会议录音、语音备忘等 |
| `src/images/` | 图片文件 | 截图、照片、图表、设计稿等 |
| `src/pdfs/` | PDF 文档 | 论文、报告、电子书、扫描件等 |
| `src/videos/` | 视频文件 | 录屏、教程视频、会议录像等 |

### 规则

- `src/` 下的文件不会被 auto_index.py 处理，不会同步到 OpenViking
- wiki 页面中引用 src/ 材料时使用相对路径，如 `src/agency-agents/engineering/engineering-backend-architect.md`
- `src/` 是只读存档，agent 不应修改其中的内容
- 新增外部资源时，按类型放入对应子目录；类型不明确的放入 `src/articles/`
