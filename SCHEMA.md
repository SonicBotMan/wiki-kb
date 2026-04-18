# Wiki Schema

> Version 4.0 — 2026-04-18
> 基于 GBrain Compiled Truth + Timeline 模式，适配 Hermes + OpenViking 本地架构

## 设计哲学

两个核心原则：

1. **Compiled Truth + Timeline** — 页面分上下两层。上面是当前最佳理解（可重写），下面是 append-only 证据线（只追加不修改）
2. **Agent 写、人读** — 知识维护成本趋近于零。Agent 自动提取实体、更新页面、维护交叉引用。人类随时可以直接编辑任何 markdown 文件

### v4 核心变更

| 维度 | v3 | v4 |
|------|-----|-----|
| 页面类型 | 9 种 (concept, entity, person, project, meeting, idea, comparison, query, tool) | **3 种** (concept, entity, person) |
| 自动化脚本 | dream_cycle, memory_to_wiki, auto_index (7 个) | **按需手动** (health_monitor, quality_check) |
| 搜索 | OpenViking only | OpenViking + **本地文件搜索自动降级** |
| 质量门控 | Dream Cycle 审核 | wiki_review (summary ≥50 chars, key_facts ≥2) |
| 目录结构 | 9 个内容目录 | **3 个内容目录** + system/ + raw/ |

> **简化原则**：tool/idea/guide/project/meeting/comparison/query 全部归入 concept 或 entity。类型越少，Agent 判断成本越低，一致性越高。

---

## Page Structure (v4)

```markdown
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: concept | entity | person
tags: [from taxonomy]
sources: [raw/articles/source-name.md]
status: active | draft | archived
---

# Page Title

## Executive Summary
一段话总结：这是什么，为什么重要，当前状态是什么。
存放当前最佳理解，新信息到来时整体重写，不是追加。

## Key Facts
- 事实1（结构化，可被 Agent 直接引用）
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
| Frontmatter | **REWRITE** | 结构化元数据，Agent 自由更新 |
| Executive Summary | **REWRITE** | 当前综合，≥50 字符 |
| Key Facts | **REWRITE** | 结构化事实列表，≥2 条 |
| Relations | **REWRITE** | 关系表，可增删改 |
| `---` (分隔线) | **固定** | 不可删除，是分层的标志 |
| Timeline | **APPEND-ONLY** | 严格只追加，永远不修改或删除已有条目 |

### Executive Summary 规则

- 存放当前对实体的最佳理解，一段话
- 每当新信息改变理解时，整体重写（不是追加）
- 来源：从 Timeline 中的事实综合得出
- 验证：summary 中的每个论断都应能在 Timeline 中找到证据
- **最低质量**：≥50 字符（wiki_review 强制检查）

### Key Facts 规则

- 结构化事实列表，每条可直接被 Agent 引用
- **最低数量**：≥2 条（wiki_review 强制检查）
- 事实可增删改，但应能在 Timeline 中找到来源

### Timeline 规则

- **严格 append-only**，永远不修改或删除已有条目
- 格式：`- **YYYY-MM-DD** | 事件描述 \n  [Source: 来源]`
- 每条 Timeline 条目必须标注来源
- 如果 Executive Summary 与 Timeline 矛盾 → 以 Timeline 为准，重写 Summary

---

## Page Types (v4)

| type | Directory | Description | 示例 |
|------|-----------|-------------|------|
| `concept` | `concepts/` | 心智模型、框架、技术概念、方法论、深度分析 | "Karpathy LLM Wiki 为什么比 RAG 高一个维度" |
| `entity` | `entities/` | 产品、工具、平台、组织、项目 | "Hermes Agent"、"OpenViking"、"百家号AI" |
| `person` | `people/` | 人物 | "何罡" |

### 分类决策规则

- **人物**（有明确个人身份） → `person`
- **产品/工具/平台/组织**（有明确实体边界） → `entity`
- **其他所有**（概念、方法论、分析、想法、对比、会议纪要） → `concept`

> 简单规则：**能被实体注册表管理的 → entity，不能的 → concept**。

---

## Typed Links

页面底部 `## Relations` 区使用类型化关系：

| 关系类型 | 含义 | 示例 |
|----------|------|------|
| `uses` | 使用/依赖 | hermes-agent → uses → OpenViking |
| `part-of` | 属于/子集 | 人民号项目 → part-of → 百家号 |
| `related` | 相关 | OpenViking → related → LLM Wiki |
| `contrasts` | 对比/竞争 | OpenClaw → contrasts → Hermes |
| `implements` | 实现/落地 | AI Copilot 理念 → implements → 百家号AI |
| `created-by` | 创建者 | 百家号AI → created-by → 何罡 |
| `evolved-from` | 演化自 | Hermes v0.8 → evolved-from → Hermes v0.7 |

正文中使用 `[[wikilinks]]` 交叉引用。

---

## Directory Structure

```
wiki/
├── concepts/       — 心智模型、框架、技术概念、深度分析（默认类型）
├── entities/       — 产品、工具、平台、组织、项目
├── people/         — 人物（只读，不自动创建新页）
├── system/         — 系统页面（wiki-health 等，不参与搜索）
├── raw/            — 原始素材（PPT、PDF、文章，不参与搜索）
├── logs/           — 运行日志
├── scripts/        — 自动化脚本（bind mount）
└── registry.json   — 实体注册表
```

### 排除目录（不参与搜索和列表）

`_EXCLUDE_DIRS`: dream-reports, raw, src, logs, scripts, .git, assets, papers, transcripts, articles, images, pdfs, videos, audio, system, tests

---

## Status Lifecycle

`draft` → `active` → `archived`

- Agent 自动创建的页面标记为 `draft`
- `wiki_review` 质量检查通过后提升为 `active`（summary ≥50 chars, key_facts ≥2）
- 内容被完全替代时归档（移出内容目录）

---

## Conventions

- **文件名**: lowercase, hyphens, no spaces (e.g., `transformer-architecture.md`)
- **每个页面** 必须以 YAML frontmatter 开头
- **Wikilinks**: `[[page-slug]]` 用于交叉引用
- **更新日期**: 每次修改页面时更新 frontmatter 中的 `updated`
- **Git**: 每次写入操作自动 commit，`wiki_undo` 可回滚

---

## Update Policy

当新信息与现有内容冲突时：

1. **Timeline 优先** — 将新信息 append 到 Timeline，标注来源
2. **重写 Summary** — 如果新信息改变理解，重写 Executive Summary
3. **保留矛盾** — 如果冲突无法解决，在 Key Facts 中标注两条互相矛盾的事实及各自来源
4. **人工裁决** — 标记需要人类判断的矛盾

---

## Notion Sync Mapping (Optional)

| Wiki type | Notion database |
|-----------|----------------|
| entity | Entities |
| concept | Concepts |

> v4 简化：仅 entity 和 concept 同步到 Notion。person 和 system 不同步。
