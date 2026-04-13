# Wiki Schema

> Version 3 — 2026-04-12
> 基于 GBrain Compiled Truth + Timeline 模式，适配 Hermes + OpenViking 本地架构

## 设计哲学

两个核心原则：
1. **Compiled Truth + Timeline** — 页面分上下两层。上面是当前最佳理解（可重写），下面是 append-only 证据线（只追加不修改）
2. **Agent 写、人读** — 知识维护成本趋近于零。agent 自动提取实体、更新页面、维护交叉引用。人类随时可以直接编辑任何 markdown 文件

---

## Page Structure (v3)

```markdown
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | person | project | meeting | idea
tags: [from taxonomy]
sources: [raw/articles/source-name.md]
status: active | draft | archived
---

# Page Title

## Executive Summary
一段话总结：这是什么，为什么重要，当前状态是什么。
这里存放当前最佳理解，新信息到来时整体重写，不是追加。

## Key Facts
- 事实1（结构化，可被 agent 直接引用）
- 事实2
- 事实3

## Relations
| 关系 | 目标 | 说明 |
|------|------|------|
| related | [[other-page]] | 说明 |

---

## Timeline

- **YYYY-MM-DD** | 事件描述
  [Source: 来源, 时间]
- **YYYY-MM-DD** | 另一个事件
  [Source: 来源, 时间]
```

### 结构规则

| 区域 | 操作 | 说明 |
|------|------|------|
| Frontmatter | **REWRITE** | 结构化元数据，agent 自由更新 |
| Executive Summary | **REWRITE** | 当前综合，新信息到来时重写 |
| Key Facts | **REWRITE** | 结构化事实列表，可增删改 |
| Relations | **REWRITE** | 关系表，可增删改 |
| --- (分隔线) | **固定** | 不可删除，是分层的标志 |
| Timeline | **APPEND-ONLY** | 严格只追加，永远不修改或删除已有条目 |

### Executive Summary 规则
- 存放当前对实体的最佳理解，一段话
- 每当新信息改变理解时，整体重写（不是追加）
- 来源：从 Timeline 中的事实综合得出
- 验证：summary 中的每个论断都应能在 Timeline 中找到证据

### Timeline 规则
- **严格 append-only**，永远不修改或删除已有条目
- 格式：`- **YYYY-MM-DD** | 事件描述 \n  [Source: 来源]`
- 每条 Timeline 条目必须标注来源
- 当 Timeline 积累到 20+ 条时，agent 应将旧条目综合进 Executive Summary，但 Timeline 本身不删减
- 如果 Executive Summary 与 Timeline 矛盾 → 以 Timeline 为准，重写 Summary

---

## Typed Links

页面底部 `## Relations` 区使用类型化关系：

| 关系类型 | 含义 | 示例 |
|----------|------|------|
| `uses` | 使用/依赖 | hermes-agent → uses → OpenViking |
| `part-of` | 属于/子集 | 人民号项目 → part-of → 百家号 |
| `related` | 相关 | OpenViking → related → LLM Wiki |
| `contrasts` | 对比/竞争 | OpenClaw → contrasts → Hermes |
| `implements` | 实现/落地 | AI Copilot理念 → implements → 百家号AI |
| `created-by` | 创建者 | Project X → created-by → Alice |
| `evolved-from` | 演化自 | Hermes v0.8 → evolved-from → Hermes v0.7 |

正文中使用 `[[wikilinks]]` 交叉引用。每个页面至少有 2 个出站链接。

---

## Directory Structure

```
wiki/
├── concepts/       — 心智模型、框架、技术概念、方法论
├── entities/       — 人、组织、产品、项目（后续按需拆分为 people/ projects/）
├── people/         — 人物（同事、合作伙伴、行业人物）
├── projects/       — 项目（工作项目、个人项目、开源项目）
├── meetings/       — 会议记录、讨论纪要、决策记录
├── ideas/          — 创意、想法、灵感、待探索方向
├── comparisons/    — 对比分析
├── queries/        — 查询记录和回答
├── raw/            — 原始素材（PPT、PDF、文章）
├── scripts/        — 自动化脚本
├── dream-reports/  — Dream Cycle 审计报告
├── SCHEMA.md       — 本文件
├── index.md        — 内容目录
├── log.md          — 变更日志
├── graph.json      — 知识图谱
└── .auto_index_state.json — 索引状态
```

**分类决策**：参见 `RESOLVER.md`。简版：人物 → `people/`；项目 → `projects/`；产品/组织 → `entities/`；概念 → `concepts/`；会议 → `meetings/`；想法 → `ideas/`；对比 → `comparisons/`。

---

## Conventions

- **File names**: lowercase, hyphens, no spaces (e.g., `transformer-architecture.md`)
- **Every page** starts with YAML frontmatter
- **Wikilinks**: `[[page-slug]]` for cross-references
- **Updated date**: 每次修改页面时更新 frontmatter 中的 `updated`
- **Status lifecycle**: `draft` → `active` → `archived`
  - agent 自动创建的页面标记为 `draft`
  - Dream Cycle 审核后提升为 `active`
  - 内容被完全替代时归档到 `_archive/`
- **New pages**: 必须添加到 `index.md` + `log.md`
- **Graph**: 修改 Relations 后运行 `auto_index.py` 更新 graph.json

---

## Tag Taxonomy

- **Models**: model, architecture, benchmark, training
- **People/Orgs**: person, company, lab, open-source, team
- **Techniques**: optimization, fine-tuning, inference, alignment, data
- **Products**: product, ai-product, content-platform, copilot, agent
- **Work**: project, meeting, review, performance, career, decision
- **Meta**: comparison, timeline, controversy, prediction, knowledge-management

Rule: every tag must appear in this taxonomy. New tag → add here first → use it.

---

## Update Policy

当新信息与现有内容冲突时：
1. **Timeline 优先** — 将新信息 append 到 Timeline，标注来源
2. **重写 Summary** — 如果新信息改变理解，重写 Executive Summary
3. **保留矛盾** — 如果冲突无法解决，在 Key Facts 中标注两条互相矛盾的事实及各自来源
4. **Flag for review** — 在 Dream Cycle 审计中标记需要人类裁决的矛盾

---

## Notion Sync Mapping

| Wiki type | Notion database |
|-----------|----------------|
| entity | Entities |
| concept | Concepts |
| comparison | Comparisons |
| query | Queries |

## New Directory Sync (2026-04-13)

以下新目录暂不同步到 Notion（Notion 端需要新建对应 database）：
- `people/` → 未来映射到 Notion People database
- `projects/` → 未来映射到 Notion Projects database
- `meetings/` → 未来映射到 Notion Meetings database
- `ideas/` → 未来映射到 Notion Ideas database
