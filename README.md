# Wiki Knowledge Base

> **Agent writes, human reads** — A structured knowledge base that AI agents maintain autonomously via MCP.

Wiki KB is a structured knowledge base built on pure Markdown files, exposing operations via [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) so that AI agents (Claude, GPT, GLM, etc.) can **actively read and write** knowledge during conversations.

## Why Wiki KB?

### The Problem

| Approach | Problem |
|----------|---------|
| **Notion / Confluence** | AI can only "read", never "write". Data locked in proprietary platforms. |
| **Obsidian / Logseq** | Humans can edit, but AI cannot proactively write during conversations. |
| **RAG (Vector DBs)** | Retrieval only — no knowledge accumulation. Knowledge lost when conversation ends. |
| **Dify / Coze / LangChain** | Tied to specific frameworks and LLMs. Knowledge structure dictated by tool's data model. |

### Core Philosophy

**Agent writes, human reads.**

Most knowledge management systems assume humans are knowledge producers and AI is a consumer. Wiki KB inverts this — **AI agents are the day-to-day maintainers**, and humans only review and edit when needed.

- You mention a new project → Agent automatically creates a wiki page
- You correct a fact → Agent immediately updates the page + logs it in the Timeline
- A valuable decision emerges from discussion → Agent extracts and archives it automatically
- **You never manually "take notes"** — knowledge maintenance cost approaches zero

### Unique Design

**1. Compiled Truth + Timeline — Solving AI Hallucination**

Every Wiki KB page has two layers:
- **Upper layer (Compiled Truth)**: Current best understanding. Rewritten as a whole when new information arrives.
- **Lower layer (Timeline)**: Append-only evidence chain. Each entry is timestamped and sourced, never modified.

When the upper conclusion contradicts the lower evidence, **Timeline takes precedence**. This fundamentally solves the trustworthiness problem of AI-generated content — not through "better prompts", but through structural constraints.

**2. MCP Native — Zero Framework Lock-in**

Wiki KB is a standard MCP Server. Any MCP-compatible agent (Claude Desktop, Cursor, Hermes, OpenHands, etc.) can directly call its 15 tools. No SDK, no adapters, no agent code changes needed.

**3. Pure Markdown + Filesystem — Your Data, Always**

All knowledge lives as `.md` files. Edit with any text editor, version control with Git, sync across machines. No database, no proprietary format, no platform lock-in.

**4. Progressive Complexity**

- **Minimal deploy**: Just Docker — no OpenViking, no LLM API key needed. Wiki CRUD and file search work out of the box.
- **+ OpenViking**: Semantic search with natural language queries.

## Architecture

### Position in Agent Memory Systems

Wiki-KB is the **structured long-term memory layer** in an agent memory system:

```
Conversation → [Agent] → MCP → Wiki KB (structured knowledge)
                            ↑
                     OpenViking (semantic search, optional)
```

### Data Flow

1. Knowledge emerges in conversation → Agent calls `wiki_create` / `wiki_update` via MCP
2. `wiki_search` queries OpenViking (semantic) with local file search fallback
3. Human → directly edits `.md` files at any time

## Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/SonicBotMan/wiki-kb.git
cd wiki-kb
cp .env.example .env
# Edit .env with your settings
```

### 2. Docker Deployment

```bash
docker compose up -d --build
docker ps --filter name=wiki-brain --format "{{.Status}}"
```

### 3. Verify MCP Endpoint

```bash
curl -s -X POST http://localhost:8764/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

## Directory Structure

```
wiki-kb/
├── scripts/                    # Core scripts (bind-mounted into container)
│   ├── wiki_mcp_server.py      # MCP Server (15 tools)
│   ├── wiki_config.py          # Centralized config
│   ├── wiki_utils.py           # Shared utilities (frontmatter, relations)
│   ├── entity_registry.py      # Entity registry (ID + alias management)
│   ├── wiki_health_monitor.py  # Health monitoring script
│   ├── wiki_quality_check.py   # Page quality validation
│   ├── wiki-to-notion.py       # Wiki → Notion sync (optional)
│   ├── wiki-backup.sh          # Backup script
│   └── wiki-cron-wrapper.sh    # Cron job wrapper with logging
├── tests/                      # Unit tests
├── docs/                       # Documentation assets
├── Dockerfile
├── docker-compose.yml
├── docker-compose.production.yml
├── .env.example
├── requirements.txt
├── SCHEMA.md                   # Page structure specification (v4)
└── README.md
```

### Wiki Data Layout (at runtime)

```
wiki/                           # Bind-mounted as /data in container
├── concepts/                   # Mental models, frameworks, concepts, analyses
├── entities/                   # Products, tools, platforms, organizations
├── people/                     # People (read-only, no auto-creation)
├── system/                     # System pages (wiki-health, etc.)
├── raw/                        # Raw materials (excluded from indexing)
├── logs/                       # Runtime logs
├── scripts/                    # Same scripts (bind-mount source)
└── registry.json               # Entity registry
```

## MCP Tools (15)

### Wiki Operations (10)

| Tool | Description |
|------|-------------|
| `wiki_search` | Semantic search across wiki pages (OpenViking + local fallback) |
| `wiki_get` | Read full page content (summary, key facts, relations, timeline) |
| `wiki_create` | Create new page (auto-routes directory + registers entity) |
| `wiki_update` | Update specific section (executive_summary, key_facts, relations) |
| `wiki_append_timeline` | Append Timeline entry (auto-formatted, date stamped) |
| `wiki_list` | List pages (supports type / status filtering) |
| `wiki_health` | Health check (registry integrity, disk, OpenViking connectivity) |
| `wiki_review` | AI-assisted quality review (promotes draft → active) |
| `wiki_undo` | Revert last N `[wiki-brain]` git commits |
| `wiki_log` | Show recent `[wiki-brain]` git commit history |

### Entity Registry (4)

| Tool | Description |
|------|-------------|
| `entity_resolve` | Resolve entity by name or alias |
| `entity_register` | Register new entity |
| `entity_list` | List entities (supports type filtering) |
| `entity_merge` | Merge two entities |

### System (1)

| Tool | Description |
|------|-------------|
| `wiki_stats` | Wiki statistics (page counts, type breakdown, registry stats) |

### Client Configuration

**Any MCP client:**
```json
{
  "mcpServers": {
    "wiki-brain": {
      "url": "http://localhost:8764/mcp",
      "timeout": 60
    }
  }
}
```

## Page Format (SCHEMA v4)

Every wiki page follows the **Compiled Truth + Timeline** pattern:

```markdown
---
title: Entity Name
created: 2026-01-15
updated: 2026-04-18
type: entity
tags: [ai-product, agent]
sources: [raw/articles/source.md]
status: active
---

# Entity Name

## Executive Summary
Current best understanding. Rewritten as a whole when new information arrives.

## Key Facts
- Fact 1 (structured, directly referenceable by agents)
- Fact 2

## Relations
| Relation | Target | Description |
|----------|--------|-------------|
| uses | [[openviking]] | Semantic search backend |

---

## Timeline

- **2026-01-15** | Page created
  [Source: wiki_mcp_server]
- **2026-03-20** | New version released
  [Source: Official blog]
```

### Core Rules

| Zone | Operation | Description |
|------|-----------|-------------|
| Frontmatter | **REWRITE** | Structured metadata |
| Executive Summary | **REWRITE** | Current best understanding, ≥50 chars |
| Key Facts | **REWRITE** | Structured fact list, ≥2 items |
| Relations | **REWRITE** | Relationship table |
| `---` (separator) | **FIXED** | Must not be removed |
| Timeline | **APPEND-ONLY** | Strictly append-only, never modify |

### Page Types (v4)

| type | Directory | Description |
|------|-----------|-------------|
| `concept` | `concepts/` | Mental models, frameworks, technical concepts, analyses |
| `entity` | `entities/` | Products, tools, platforms, organizations, projects |
| `person` | `people/` | People (read-only, no auto-creation) |

> **v4 simplification**: Previous types (tool, idea, guide, project, meeting, comparison, query) are consolidated into `concept` or `entity`. This keeps the schema minimal while maintaining expressive power.

### Status Lifecycle

`draft` → `active` → `archived`

- Agent-created pages start as `draft`
- `wiki_review` promotes to `active` after quality check (summary ≥50 chars, key_facts ≥2 items)
- Pages archived when content is fully superseded

## Search

### Default: File Search

When OpenViking is unavailable, `wiki_search` falls back to local file search (substring matching on filenames and content).

### OpenViking Semantic Search (Optional)

With OpenViking configured, `wiki_search` uses vector semantic search with automatic fallback:

1. Wiki pages are synced to OpenViking
2. OpenViking auto-extracts semantics + vectorizes
3. `wiki_search` calls OpenViking API, filters out unresolvable results
4. If zero valid results, automatically falls back to local file search

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WIKI_ROOT` | No | `/data` | Wiki data root (inside container) |
| `MCP_PORT` | No | `8764` | MCP Server port |
| `OPENVIKING_ENDPOINT` | No | `http://localhost:1933` | OpenViking URL |
| `OPENVIKING_API_KEY` | No | — | OpenViking API Key |
| `OPENVIKING_ACCOUNT` | No | `hermes` | OpenViking account |
| `OPENVIKING_USER` | No | `default` | OpenViking user |
| `MCP_API_KEY` | No | — | MCP auth key (skip if unset, recommended for LAN) |
| `NOTION_API_KEY` | No | — | Notion API Key (optional sync) |
| `NOTION_DB_ENTITY` | No | — | Notion Entity database ID |
| `NOTION_DB_CONCEPT` | No | — | Notion Concept database ID |

### .env.example

```env
MCP_PORT=8764
# MCP_API_KEY=your-key-here    # Optional, for non-LAN deployments

# OpenViking (optional — enables semantic search)
# OPENVIKING_ENDPOINT=http://localhost:1933
# OPENVIKING_API_KEY=your-key
# OPENVIKING_ACCOUNT=hermes
# OPENVIKING_USER=default

# Notion (optional — enables Notion sync)
# NOTION_API_KEY=your-key
# NOTION_DB_ENTITY=ntn_xxx
# NOTION_DB_CONCEPT=ntn_xxx
```

## Docker Compose

### Standalone (minimal)

```yaml
services:
  wiki-brain:
    build: .
    container_name: wiki-brain
    restart: unless-stopped
    ports:
      - "0.0.0.0:8764:8764"
    volumes:
      - ./wiki:/data
      - ./scripts:/app/scripts
      - ./.env:/app/.env:ro
    env_file:
      - .env
    environment:
      - PYTHONPATH=/app/scripts
      - PYTHONUNBUFFERED=1
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"
```

### With OpenViking (semantic search)

```yaml
services:
  wiki-brain:
    build: .
    container_name: wiki-brain
    restart: unless-stopped
    ports:
      - "0.0.0.0:8764:8764"
    volumes:
      - ./wiki:/data
      - ./scripts:/app/scripts
      - ./.env:/app/.env:ro
    env_file:
      - .env
    environment:
      - PYTHONPATH=/app/scripts
      - PYTHONUNBUFFERED=1
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

## Backup

```bash
docker exec wiki-brain bash /app/scripts/wiki-backup.sh
```

Excludes `logs/`, `__pycache__/`, `.git/`, `raw/` and other non-essential directories.

## Development

### Local Development

```bash
pip install -r requirements.txt
export WIKI_ROOT=./wiki
export MCP_PORT=8764
python scripts/wiki_mcp_server.py
```

### Modifying MCP Server

Scripts are bind-mounted — just edit and restart:

```bash
docker compose restart
```

### Running Tests

```bash
PYTHONPATH=scripts pytest tests/ -v
```

## FAQ

### Session Terminated Error

MCP StreamableHTTP uses stateful sessions. Built-in: `wiki_mcp_server.py` sets `session_idle_timeout` to 24 hours. Clients should implement auto-reconnect for long-idle periods.

### API Key Auth Not Working

Under FastMCP StreamableHTTP, `HTTP_AUTHORIZATION` may not be set correctly. Solutions:
1. LAN: Leave `MCP_API_KEY` empty (skip auth)
2. Auth required: Add `headers: { Authorization: "Bearer <key>" }` in client config

### OpenViking Search Returns No Results

`wiki_search` automatically falls back to local file search when OpenViking returns zero valid results. Check `OPENVIKING_*` env vars if semantic search is needed.

### Undo a Mistaken Change

Every write is auto-committed with `[wiki-brain]` prefix. Only these commits are affected by `wiki_undo`:

```
wiki_undo(n=1)   # Revert last change
wiki_log(limit=10) # View history
```

## License

MIT
