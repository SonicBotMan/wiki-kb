# Wiki Knowledge Base

> **Agent 写、人读** — 一个让 AI Agent 自动维护结构化知识库的系统。

Wiki KB 是一个基于纯 Markdown 文件的结构化知识库，通过 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 暴露操作接口，让 AI Agent（如 Claude、GPT、GLM 等）能在对话中**主动读写**知识。

## 为什么是 Wiki KB？

### 痛点

现有的 AI 知识管理方案各有明显短板：

| 方案 | 问题 |
|------|------|
| **Notion / Confluence** | AI 只能"读"，不能"写"。知识维护成本全在人身上。数据锁定在私有平台。 |
| **Obsidian / Logseq** | 人能编辑，但 AI 无法在对话中主动写入。需要额外插件/脚本桥接。 |
| **RAG (向量数据库)** | 只有检索，没有知识沉淀。每次对话结束，知识就丢了。 |
| **Dify / Coze / LangChain** | 绑定特定框架和 LLM。知识结构被工具的 data model 决定，不够灵活。 |
| **让 AI 写 Notion/Obsidian** | 知识没有结构化规范，AI 写的内容质量参差不齐，缺乏矛盾检测和来源追溯。 |

### 核心理念

**Agent 写、人读。**

大多数知识管理系统的设计假设**人**是知识的生产者，AI 是消费者。Wiki KB 反过来——**AI Agent 是知识的日常维护者**，人只需要在需要时审阅和编辑。

这意味着：
- 你在对话中提到一个新项目 → Agent 自动创建 wiki 页面
- 你纠正了一个事实 → Agent 立刻更新页面 + 记录到 Timeline
- 对话中产生了有价值的决策 → Agent 自动提取并归档
- **你从不手动"记笔记"**，知识维护成本趋近于零

### 独特设计

**1. Compiled Truth + Timeline — 解决 AI 幻觉问题**

传统知识库只存"当前结论"，AI 生成的内容无法追溯来源。Wiki KB 的每个页面分两层：

- **上层（Compiled Truth）**：当前最佳理解，AI 和人都可编辑，随时被重写
- **下层（Timeline）**：append-only 证据线，每条信息标注来源和时间，永不修改

当上层结论和下层证据矛盾时，**以 Timeline 为准**。这从根本上解决了 AI 生成内容不可信的问题——不是靠"更好的 prompt"，而是靠结构约束。

**2. MCP 原生 — 不绑定任何 Agent 框架**

Wiki KB 是一个标准 MCP Server。任何支持 MCP 的 Agent（Claude Desktop、Cursor、Hermes、OpenHands 等）都能直接调用它的 11 个 tools。不需要 SDK、不需要适配器、不需要改 Agent 代码。

这意味着你今天用 Claude，明天换 GPT，后天换开源模型——**知识库不变，换 Agent 零成本**。

**3. 纯 Markdown + 文件系统 — 数据永远属于你**

所有知识就是 `.md` 文件。你可以：
- 用任何编辑器直接编辑
- 用 Git 做版本管理和协作
- 用 rsync/SCP 在机器间同步
- 离线阅读、备份、迁移

没有数据库、没有私有格式、没有平台锁定。哪怕 Wiki KB 项目停止维护，你的知识仍然完整可用。

**4. 自动化质量保证**

知识由 AI 维护，但质量不能靠运气：

- **Dream Cycle**：定期用 LLM 审计全部页面，检测矛盾、过时信息、知识缺口
- **Entity Registry**：统一管理实体 ID 和别名，防止"同一个东西叫三个名字"
- **Auto Index**：自动生成知识图谱，维护实体间的交叉引用

**5. 渐进式复杂度**

不需要一次性用全部功能：

- **最小部署**：只要 Docker，不需要 OpenViking、不需要 LLM API Key，Wiki CRUD 和文件搜索就能工作
- **加 OpenViking**：语义搜索能力，支持自然语言查询
- **加 LLM API Key**：Dream Cycle 审计 + Memory Sync 自动化

每一步都是可选的，不会因为缺少某个组件就无法启动。

### 与同类项目的对比

| 维度 | Wiki KB | Notion AI | Obsidian + AI | RAG 向量库 |
|------|---------|-----------|---------------|-----------|
| AI 能否主动写入 | ✅ 核心功能 | ❌ 只能读 | ⚠️ 需插件 | ❌ 只能检索 |
| 知识结构规范 | ✅ Schema v3 | ✅ Database Schema | ❌ 自由格式 | ❌ 无结构 |
| 来源追溯 | ✅ Timeline | ❌ | ⚠️ 部分插件 | ❌ |
| 数据自主性 | ✅ 纯 Markdown | ❌ 平台锁定 | ✅ 纯 Markdown | ⚠️ 取决于实现 |
| Agent 框架绑定 | ❌ MCP 标准 | ✅ 绑定 Notion | ✅ 绑定 Obsidian | ✅ 绑定框架 |
| 自动质量审计 | ✅ Dream Cycle | ❌ | ❌ | ❌ |
| 离线可用 | ✅ 纯文件 | ❌ | ✅ | ⚠️ 取决于向量库 |
| 多 Agent 兼容 | ✅ 任何 MCP 客户端 | ❌ | ❌ | ⚠️ 取决于框架 |

## ✨ 特性

- 📝 **结构化 Schema** — "Compiled Truth + Timeline" 双层模式：上层可重写、下层只追加
- 🔌 **MCP Server** — 标准 MCP 协议，任何支持 MCP 的 Agent 都能直接调用
- 🔍 **语义搜索** — 可选集成 [OpenViking](https://github.com/openviking/openviking) 做向量检索
- 🧠 **实体注册** — 内置 Entity Registry，支持别名解析、模糊匹配、去重
- 🤖 **自动化 Pipeline** — Dream Cycle（LLM 审计）、Auto Index（知识图谱生成）、Memory Sync（对话→Wiki）
- 🐳 **Docker 部署** — 单容器部署，资源占用低（512MB / 0.5 CPU）
- 📂 **纯文件存储** — 所有数据就是 Markdown 文件，可用任何编辑器直接编辑，支持 Git 版本管理

## 架构概览

```
┌──────────────────────────────────────────────────────────┐
│                    AI Agent (Client)                      │
│  (Claude / GPT / GLM / 任何支持 MCP 的 Agent)            │
└──────────────┬───────────────────────────────────────────┘
               │ MCP StreamableHTTP
               ▼
┌──────────────────────────────────────────────────────────┐
│              Wiki KB MCP Server (:8764)                   │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────┐ │
│  │ Wiki CRUD   │ │ Entity Reg   │ │ Search (fallback) │ │
│  │ 11 tools    │ │ 5 tools      │ │ + OpenViking opt  │ │
│  └──────┬──────┘ └──────┬───────┘ └────────┬──────────┘ │
│         └────────┬──────┘                   │            │
│                  ▼                          ▼            │
│         ┌─────────────┐           ┌─────────────────┐   │
│         │ Markdown    │           │  OpenViking API │   │
│         │ 文件系统     │           │  (可选)         │   │
│         └─────────────┘           └─────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## 快速开始

### 前提条件

- Docker & Docker Compose
- Python 3.11+（如需本地运行脚本）

### 1. 克隆并配置

```bash
git clone https://github.com/yourname/wiki-kb.git
cd wiki-kb

# 创建环境配置
cp .env.example .env
# 编辑 .env 填入你的配置（见下方环境变量说明）
```

### 2. 创建 Wiki 目录结构

```bash
mkdir -p wiki/{concepts,entities,people,projects,meetings,ideas,comparisons,queries}
mkdir -p wiki/{logs/dream-reports,src/{articles,audio,images,pdfs,videos}}
mkdir -p scripts
```

### 3. Docker 部署

```bash
docker compose up -d --build

# 等待健康检查通过（约 30 秒）
docker ps --filter name=wiki-kb --format "{{.Status}}"
```

### 4. 验证 MCP 端点

```bash
curl -s -X POST http://localhost:8764/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

## 目录结构

```
wiki-kb/
├── scripts/                    # 核心脚本（构建到 Docker 镜像中）
│   ├── wiki_mcp_server.py      # MCP Server 主程序（11 个 tools）
│   ├── wiki_utils.py           # Frontmatter 解析工具
│   ├── entity_registry.py      # 实体注册表（CLI + API）
│   ├── auto_index.py           # 自动索引 + OpenViking 同步
│   ├── dream_cycle.py          # LLM 知识审计
│   ├── memory_to_wiki.py       # 对话记忆 → Wiki 同步
│   └── wiki-backup.sh          # 备份脚本
├── wiki/                       # 知识库数据（bind mount 到容器）
│   ├── concepts/               # 心智模型、框架、技术概念
│   ├── entities/               # 产品、组织、公司、平台
│   ├── people/                 # 人物
│   ├── projects/               # 项目
│   ├── meetings/               # 会议记录
│   ├── ideas/                  # 创意、想法
│   ├── comparisons/            # 对比分析
│   ├── queries/                # 查询记录
│   ├── src/                    # 原始素材（不参与索引）
│   │   ├── articles/           # 文章存档
│   │   ├── images/             # 图片
│   │   ├── pdfs/               # PDF 文档
│   │   ├── audio/              # 音频
│   │   └── videos/             # 视频
│   ├── logs/                   # 日志和审计报告
│   ├── SCHEMA.md               # 页面结构规范
│   ├── RESOLVER.md             # 分类路由规则
│   ├── graph.json              # 知识图谱
│   ├── registry.json           # 实体注册表
│   └── index.md                # 内容目录
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## MCP Tools

Wiki KB 暴露 11 个 MCP tools：

### Wiki 操作（6 个）

| Tool | 说明 |
|------|------|
| `wiki_search` | 语义搜索 wiki 页面（支持 type 过滤） |
| `wiki_get` | 读取页面完整内容（返回结构化 sections） |
| `wiki_create` | 创建新页面（自动路由目录 + 注册实体） |
| `wiki_update` | 更新指定 section（executive_summary / key_facts / relations） |
| `wiki_append_timeline` | 追加 Timeline 条目（自动格式化 + 更新日期） |
| `wiki_list` | 列出页面（支持 type / status 过滤） |

### Entity Registry（4 个）

| Tool | 说明 |
|------|------|
| `entity_resolve` | 通过名称/别名解析实体 |
| `entity_register` | 注册新实体 |
| `entity_list` | 列出实体（支持 type 过滤） |
| `entity_merge` | 合并两个实体 |

### 系统（1 个）

| Tool | 说明 |
|------|------|
| `wiki_stats` | 返回统计信息 + OpenViking 连通状态 |

### 在 Agent 中使用

在 MCP 客户端配置中添加：

```json
{
  "mcpServers": {
    "wiki-kb": {
      "url": "http://localhost:8764/mcp",
      "timeout": 60
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "wiki-kb": {
      "url": "http://localhost:8764/mcp"
    }
  }
}
```

**Cursor / VS Code** (settings.json):
```json
{
  "mcp.servers": {
    "wiki-kb": {
      "url": "http://localhost:8764/mcp"
    }
  }
}
```

## 页面格式（SCHEMA v3）

每个 wiki 页面遵循 **Compiled Truth + Timeline** 模式：

```markdown
---
title: 实体名
created: 2026-01-15
updated: 2026-04-13
type: entity
tags: [ai-product, agent]
sources: [src/articles/xxx.md]
status: active
---

# 实体名

## Executive Summary
当前最佳理解。新信息到来时整体重写，不是追加。

## Key Facts
- 事实1（结构化，可被 agent 直接引用）
- 事实2

## Relations
| 关系 | 目标 | 说明 |
|------|------|------|
| uses | [[openviking]] | 语义搜索后端 |
| related | [[hermes-agent]] | 集成 |

---

## Timeline

- **2026-01-15** | 页面创建
  [Source: wiki_mcp_server]
- **2026-03-20** | 新版本发布
  [Source: 官方博客, 2026-03-20]
```

### 核心规则

| 区域 | 操作 | 说明 |
|------|------|------|
| Frontmatter | **REWRITE** | 结构化元数据 |
| Executive Summary | **REWRITE** | 当前最佳理解，整体重写 |
| Key Facts | **REWRITE** | 结构化事实列表 |
| Relations | **REWRITE** | 关系表 |
| `---` (分隔线) | **固定** | 不可删除 |
| Timeline | **APPEND-ONLY** | 严格只追加，永远不修改 |

### 页面类型

| type | 目录 | 说明 |
|------|------|------|
| `person` | `people/` | 人物 |
| `project` | `projects/` | 项目（有明确目标和时间线） |
| `entity` | `entities/` | 产品/组织/公司/平台 |
| `concept` | `concepts/` | 心智模型/框架/技术概念 |
| `meeting` | `meetings/` | 会议记录/决策记录 |
| `idea` | `ideas/` | 创意/想法/待探索方向 |
| `comparison` | `comparisons/` | 对比分析 |
| `query` | `queries/` | 查询记录 |

### 关系类型

| 关系 | 含义 |
|------|------|
| `uses` | 使用/依赖 |
| `part-of` | 属于/子集 |
| `related` | 相关 |
| `contrasts` | 对比/竞争 |
| `implements` | 实现/落地 |
| `created-by` | 创建者 |
| `evolved-from` | 演化自 |

### 状态生命周期

`draft` → `active` → `archived`

- Agent 自动创建的页面标记为 `draft`
- Dream Cycle 审核后提升为 `active`
- 内容被完全替代时归档

## 自动化 Pipeline

### Dream Cycle — LLM 知识审计

定期用 LLM 审计知识库质量，检测：
- **矛盾** — 跨页面信息冲突
- **过时** — 信息已不再准确
- **缺口** — 缺失重要知识
- **关系问题** — 实体关系不一致

```bash
# 手动运行审计（dry-run）
docker exec wiki-kb python3 /app/scripts/dream_cycle.py

# 应用审计建议
docker exec wiki-kb python3 /app/scripts/dream_cycle.py --apply
```

### Auto Index — 知识图谱生成

检测文件变更，自动生成 `graph.json`（节点 + 边），可选同步到 OpenViking。

```bash
docker exec wiki-kb python3 /app/scripts/auto_index.py
```

### Memory to Wiki — 对话记忆同步

从 OpenViking memories 提取实体和事件，写回 Wiki 页面。

```bash
docker exec wiki-kb python3 /app/scripts/memory_to_wiki.py
```

### Cron 定时任务

```bash
# 每日 03:00 — 对话记忆同步
0 3 * * * docker exec wiki-kb python3 /app/scripts/memory_to_wiki.py >> /path/to/wiki/logs/cron.log 2>&1

# 每日 03:10 — Dream Cycle 审计
10 3 * * * docker exec wiki-kb python3 /app/scripts/dream_cycle.py --apply >> /path/to/wiki/logs/cron.log 2>&1

# 每日 03:30 — 自动索引 + OpenViking 同步
30 3 * * * docker exec wiki-kb python3 /app/scripts/auto_index.py --sync >> /path/to/wiki/logs/cron.log 2>&1

# 每日 04:00 — 备份
0 4 * * * /path/to/wiki-kb/scripts/wiki-backup.sh >> /path/to/wiki/logs/backup.log 2>&1
```

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `WIKI_ROOT` | 否 | `/data` | Wiki 数据根目录（容器内） |
| `MCP_PORT` | 否 | `8764` | MCP Server 端口 |
| `OPENVIKING_HOST` | 否 | `localhost` | OpenViking 主机名 |
| `OPENVIKING_PORT` | 否 | `1933` | OpenViking 端口 |
| `OPENVIKING_API_KEY` | 否 | — | OpenViking API Key（不设置则跳过 OV 集成） |
| `OPENVIKING_ACCOUNT` | 否 | `hermes` | OpenViking 账户名 |
| `OPENVIKING_USER` | 否 | `default` | OpenViking 用户名 |
| `MCP_API_KEY` | 否 | — | MCP API Key（不设置则跳过认证） |
| `GLM_API_KEY` | Dream Cycle | — | LLM API Key（Dream Cycle 审计用） |
| `GLM_BASE_URL` | Dream Cycle | — | LLM API Base URL |
| `LLM_MODEL` | Dream Cycle | `glm-4-flash` | Dream Cycle 使用的 LLM 模型 |

### .env.example

```env
# === MCP Server ===
MCP_PORT=8764
# MCP_API_KEY=your-secret-key    # 不设置则跳过认证（内网环境推荐）

# === OpenViking (可选) ===
# OPENVIKING_HOST=openviking
# OPENVIKING_PORT=1933
# OPENVIKING_API_KEY=your-ov-key
# OPENVIKING_ACCOUNT=hermes
# OPENVIKING_USER=default

# === LLM (Dream Cycle 需要) ===
# GLM_API_KEY=your-glm-key
# GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# LLM_MODEL=glm-4-flash
```

## Docker Compose 配置

```yaml
services:
  wiki-kb:
    build: .
    container_name: wiki-kb
    restart: unless-stopped
    ports:
      - "0.0.0.0:8764:8764"
    volumes:
      - ./wiki:/data                    # Wiki 数据
      - ./.env:/app/.env:ro             # 环境变量
    env_file:
      - .env
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

**与 OpenViking 联合部署**（可选）：

```yaml
services:
  wiki-kb:
    build: .
    container_name: wiki-kb
    restart: unless-stopped
    ports:
      - "0.0.0.0:8764:8764"
    volumes:
      - ./wiki:/data
      - ./.env:/app/.env:ro
    env_file:
      - .env
    networks:
      - wiki-net

  openviking:
    image: openviking/openviking:latest
    container_name: openviking
    restart: unless-stopped
    ports:
      - "0.0.0.0:1933:1933"
    volumes:
      - ./openviking-data:/data
    networks:
      - wiki-net

networks:
  wiki-net:
    driver: bridge
```

## Entity Registry

内置实体管理系统，维护唯一 ID + 别名索引：

```json
{
  "entities": {
    "ent_a1b2c3": {
      "id": "ent_a1b2c3",
      "canonical_name": "OpenViking",
      "aliases": ["openviking", "ov", "viking"],
      "type": "entity",
      "primary_page": "entities/openviking.md",
      "created": "2026-01-15",
      "updated": "2026-04-13"
    }
  },
  "alias_index": {
    "openviking": "ent_a1b2c3",
    "ov": "ent_a1b2c3"
  },
  "stats": {
    "total_entities": 18,
    "total_aliases": 42
  }
}
```

### CLI 命令

```bash
# 扫描所有 wiki 页面，自动注册未注册的实体
docker exec wiki-kb python3 -m entity_registry scan

# 列出所有实体
docker exec wiki-kb python3 -m entity_registry list

# 搜索实体
docker exec wiki-kb python3 -m entity_registry search "openviking"

# 检测重复实体
docker exec wiki-kb python3 -m entity_registry dedup

# 合并实体
docker exec wiki-kb python3 -m entity_registry merge ent_a1b2c3 ent_d4e5f6

# 重建索引
docker exec wiki-kb python3 -m entity_registry rebuild
```

## 搜索

### 文件搜索（默认）

OpenViking 不可用时，自动 fallback 到本地文件搜索（substring 匹配）。

### OpenViking 语义搜索（可选）

配置 OpenViking 后，`wiki_search` 使用向量语义搜索：

1. `auto_index.py --sync` 将 wiki 页面同步到 OpenViking
2. OpenViking 自动做语义提取 + 向量化
3. `wiki_search` 调用 OpenViking API 返回语义相关结果

## 备份

```bash
# 手动备份
docker exec wiki-kb bash /app/scripts/wiki-backup.sh

# 保留最近 3 个备份
docker exec wiki-kb bash /app/scripts/wiki-backup.sh --keep 3
```

备份排除 `logs/`、`__pycache__/`、`.git/`、`src/agency-agents/` 等大文件目录。

## 不依赖 OpenViking 的最小部署

如果你不需要语义搜索，可以只部署 Wiki KB MCP Server：

```yaml
# docker-compose.minimal.yml
services:
  wiki-kb:
    build: .
    container_name: wiki-kb
    restart: unless-stopped
    ports:
      - "0.0.0.0:8764:8764"
    volumes:
      - ./wiki:/data
      - ./.env:/app/.env:ro
    env_file:
      - .env
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: "0.25"
```

对应的 `.env`：
```env
MCP_PORT=8764
# 不设置 OPENVIKING_* 变量，搜索自动 fallback 到文件搜索
# 不设置 GLM_* 变量，Dream Cycle 不可用但 Wiki CRUD 正常
```

## 开发

### 本地运行（不用 Docker）

```bash
cd wiki-kb

# 安装依赖
pip install mcp pyyaml requests

# 设置环境变量
export WIKI_ROOT=./wiki
export MCP_PORT=8764

# 启动 MCP Server
python scripts/wiki_mcp_server.py
```

### 添加新脚本

1. 在 `scripts/` 目录创建新脚本
2. 确保使用 `WIKI_ROOT` 环境变量定位 wiki 数据目录
3. 如果需要运行 entity_registry，import 前插入 `sys.path.insert(0, str(Path(__file__).parent))`
4. 重新构建 Docker 镜像：`docker compose build --no-cache && docker compose up -d`

### 修改 MCP Server

编辑 `scripts/wiki_mcp_server.py`，然后：

```bash
docker compose build --no-cache && docker compose up -d
```

> ⚠️ `scripts/` 是通过 Dockerfile `COPY` 打包进镜像的，不是 bind mount。修改后必须 rebuild。

## 常见问题

### Session Terminated 错误

MCP StreamableHTTP 使用有状态 session。长时间空闲后 session 可能过期。

**已内置修复**：`wiki_mcp_server.py` 通过 monkey-patch 将 `session_idle_timeout` 设为 24 小时。

如果问题仍然出现，客户端侧应实现自动重连逻辑。

### API Key 认证不生效

FastMCP StreamableHTTP 模式下，`HTTP_AUTHORIZATION` 环境变量可能不被正确设置。

**解决方案**：
1. 内网环境：清空 `MCP_API_KEY=`，跳过认证
2. 需要认证：在客户端配置中添加 `headers: { Authorization: "Bearer <key>" }`

### 端口绑定问题

如果 Wiki KB 和客户端不在同一台机器上，确保端口绑定 `0.0.0.0` 而不是 `127.0.0.1`。

### OpenViking 同步失败

`auto_index.py --sync` 使用两步 API：
1. `POST /api/v1/resources/temp_upload` — 上传文件
2. `POST /api/v1/resources` — 添加到 OpenViking

确保 `OPENVIKING_API_KEY`、`OPENVIKING_ACCOUNT`、`OPENVIKING_USER` 配置正确。

## License

MIT
