# Wiki Knowledge Base

**Knowledge that compounds.** A structured knowledge system where AI agents actively maintain, improve, and compound understanding over time — not just retrieve it.

---

## The Problem We're Solving

Every AI application hits the same wall: **knowledge doesn't accumulate.**

RAG retrieves the same documents every time. Chat history gets compressed and forgotten. The agent answers the same question differently each session. Nothing compounds.

Karpathy named this in April 2026: *"LLMs re-derive knowledge from scratch on every query. There's no accumulation."* His LLM Wiki proposed a shift — **compile knowledge at write-time, not retrieve at query-time** — and his GitHub Gist got 14,000 stars in a week.

Garry Tan built [gbrain](https://github.com/garrytan/gbrain) around the same insight: every entity gets a Markdown file with a **compiled truth** (current best understanding) on top and an **append-only timeline** (evidence chain) on the bottom. When truth and evidence conflict, evidence wins. Structure defeats hallucination.

We started from these ideas. Then we spent three months running a production knowledge base — 58 pages created, automated scripts built, schema versions iterated — and learned what actually works and what doesn't.

**This project is what we learned.**

---

## What Makes Wiki KB Different

### 1. Born from Production, Not Theory

Most knowledge management projects are designed first, deployed never. We ran Wiki KB in production for months with a real AI agent (Hermes) maintaining knowledge from daily conversations, research sessions, and project work. The v4 architecture is the result of cutting everything that sounded good but didn't produce value:

| We built | What happened | Decision |
|----------|---------------|----------|
| Dream Cycle (LLM audit) | Cron ran but produced surface-level feedback. Real quality came from targeted wiki_review on individual pages. | **Killed.** Manual review > automated audit. |
| memory_to_wiki sync | OpenViking search API doesn't index memories. Script ran but wrote nothing. | **Killed.** Fix the upstream first. |
| auto_index (knowledge graph) | graph.json generated but nothing consumed it. Relations in Markdown wikilinks are sufficient. | **Killed.** YAGNI. |
| 9 page types | Agents confused about routing. `tool` vs `entity` vs `project` — same thing, different label. | **3 types.** concept, entity, person. |
| Cron pipeline (7 jobs) | Only 2 STARTED entries in cron.log after weeks. | **No cron.** Agent triggers on demand. |

> **The schema IS the product.** Fewer types means the agent classifies correctly more often. Fewer scripts means less maintenance. Fewer cron jobs means less dead code.

### 2. Compiled Truth + Timeline — Structure Beats Prompts

Every page has two layers:

```
┌─────────────────────────────┐
│  COMPILED TRUTH (rewritable) │  ← Current best understanding
│  Executive Summary           │     Rewritten as a whole, never appended
│  Key Facts                   │
│  Relations                   │
├─────────────────────────────┤
│  TIMELINE (append-only)      │  ← Evidence chain
│  - 2026-04-12 | Event...     │     Timestamped, sourced, immutable
│  - 2026-04-15 | Update...    │
└─────────────────────────────┘
```

When the summary contradicts the timeline, **timeline wins**. This isn't a prompt engineering trick — it's a structural constraint that makes AI-generated knowledge auditable and self-correcting.

Quality gates enforce this: `wiki_review` promotes draft → active only when summary ≥50 chars and key_facts ≥2 items. In our v4 refactoring, this took quality from 13% (8/58 pages passing) to 100% (40/40).

### 3. Three Types. Not Nine.

Previous versions had concept, entity, person, project, meeting, idea, comparison, query, tool. The agent constantly misrouted pages. The real insight: **the type system should reduce cognitive load, not increase it.**

| Type | Directory | What goes here |
|------|-----------|----------------|
| `concept` | `concepts/` | Everything that's not a concrete entity — frameworks, analyses, methodologies, comparisons, meeting notes, ideas. The default. |
| `entity` | `entities/` | Things with clear boundaries — products, tools, platforms, organizations. Anything the entity registry can manage. |
| `person` | `people/` | People. Read-only — agents don't auto-create person pages. |

Simple rule: **if you can register it in the entity registry, it's an entity. Otherwise, it's a concept.**

### 4. Graceful Degradation Everywhere

Production taught us that dependencies fail. OpenViking goes down, NAS python breaks, MCP sessions expire. Every component has a fallback:

- **Search**: OpenViking semantic search → local file search (automatic, zero config)
- **Auth**: API key set → Bearer auth. Unset → open access (safe for LAN)
- **Session**: MCP StreamableHTTP idle timeout → 24 hours (monkey-patched)
- **Quality**: OpenViking returns garbage "Untitled" results → filter + fall back to local

The system is designed to be **maximally useful with minimal dependencies**. Docker + Markdown files = everything works. OpenViking = search gets smarter. That's it.

### 5. MCP Native — Knowledge as a Tool

Wiki KB is a standard MCP Server with 15 tools. Any MCP-compatible agent can call it directly — no SDK, no adapter, no framework lock-in. The agent doesn't "query a knowledge base" — it **uses knowledge tools** alongside every other tool it has.

```
Agent tool belt: web_search, file_edit, terminal, ... wiki_search, wiki_create, wiki_update
```

This means switching from Claude to GPT to an open-source model costs nothing — your knowledge stays, the tools stay, only the model changes.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   AI Agent                       │
│  (Claude / GPT / GLM / any MCP-compatible)      │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ wiki_    │  │ wiki_    │  │ entity_       │  │
│  │ search   │  │ create   │  │ resolve       │  │
│  │ wiki_get │  │ wiki_    │  │ entity_register│  │
│  │ wiki_    │  │ update   │  │ entity_list   │  │
│  │ list     │  │ wiki_    │  │ entity_merge  │  │
│  │ wiki_    │  │ review   │  │               │  │
│  │ health   │  │ wiki_    │  │               │  │
│  │ wiki_    │  │ undo     │  │               │  │
│  │ stats    │  │ wiki_log │  │               │  │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │              │                │          │
└───────┼──────────────┼────────────────┼──────────┘
        │         MCP (HTTP)            │
        ▼              ▼                ▼
┌─────────────────────────────────────────────────┐
│              Wiki KB MCP Server                   │
│                                                  │
│  ┌──────────────┐  ┌──────────────────────────┐  │
│  │   Markdown   │  │     Entity Registry      │  │
│  │   Wiki Pages │  │  (ID + Alias Index)      │  │
│  │              │  │                          │  │
│  │ concepts/    │  │  openviking → ent_a1b2c3 │  │
│  │ entities/    │  │  ov → ent_a1b2c3         │  │
│  │ people/      │  │  hermes → ent_d4e5f6     │  │
│  └──────┬───────┘  └──────────────────────────┘  │
│         │                                        │
│    ┌────┴────┐                                   │
│    │  Git    │  Auto-commit on every write       │
│    └─────────┘                                   │
└─────────────────┬───────────────────────────────┘
                  │ (optional)
                  ▼
┌─────────────────────────────────────────────────┐
│              OpenViking                          │
│         Semantic Search Backend                  │
│    (vector search + local fallback)              │
└─────────────────────────────────────────────────┘
```

---

## Quick Start

### Minimal (just Docker)

```bash
git clone https://github.com/SonicBotMan/wiki-kb.git
cd wiki-kb
cp .env.example .env
docker compose up -d --build

# Verify
curl -s -X POST http://localhost:8764/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

That's it. Wiki CRUD and file search work immediately. No LLM API key, no vector database, no external services.

### With Semantic Search (+ OpenViking)

Add to `.env`:
```env
OPENVIKING_ENDPOINT=http://localhost:1933
OPENVIKING_API_KEY=your-key
OPENVIKING_ACCOUNT=hermes
OPENVIKING_USER=default
```

Search upgrades from substring matching to vector semantic search with automatic local fallback.

---

## MCP Tools (15)

### Knowledge Operations (10)

| Tool | What it does |
|------|-------------|
| `wiki_search` | Semantic search (OpenViking) with automatic local fallback |
| `wiki_get` | Read full page — summary, facts, relations, timeline |
| `wiki_create` | Create page → auto-route directory → register entity → draft |
| `wiki_update` | Update a section (summary / facts / relations) |
| `wiki_append_timeline` | Add timestamped, sourced event to timeline |
| `wiki_list` | List pages with type/status filtering |
| `wiki_health` | System health: registry, disk, OpenViking connectivity |
| `wiki_review` | Quality gate: promotes draft → active (summary ≥50 chars, facts ≥2) |
| `wiki_undo` | Revert last N auto-commits (only `[wiki-brain]` prefixed) |
| `wiki_log` | View commit history |

### Entity Registry (4)

| Tool | What it does |
|------|-------------|
| `entity_resolve` | Name/alias → entity (fuzzy matching) |
| `entity_register` | Register entity with ID + aliases |
| `entity_list` | List/filter entities |
| `entity_merge` | Deduplicate: merge entity A into B |

### System (1)

| Tool | What it does |
|------|-------------|
| `wiki_stats` | Page counts, type breakdown, registry stats |

---

## Page Format

```markdown
---
title: Hermes Agent
type: entity
status: active
created: 2026-04-12
updated: 2026-04-18
tags: [ai-agent, self-evolving]
---

# Hermes Agent

## Executive Summary
Self-evolving AI agent framework. Open-source CLI agent that improves its own
skills through conversation-driven learning and scheduled autonomous tasks.

## Key Facts
- 65k+ GitHub stars, active community
- Skill system: procedural memory that compounds over sessions
- Memory: built-in persistent memory + external providers (OpenViking, Mem0)
- MCP native: extensible via Model Context Protocol servers

## Relations
| Relation | Target | Description |
|----------|--------|-------------|
| uses | [[openviking]] | Semantic search backend |
| related | [[gbrain]] | Inspired compiled truth pattern |

---

## Timeline

- **2026-04-12** | Page created
  [Source: wiki_mcp_server]
- **2026-04-18** | v4 refactoring: 58→40 pages, 9→3 types
  [Source: Hermes session]
```

### The Rules

| Zone | Rule | Why |
|------|------|-----|
| Executive Summary | **Rewrite as a whole** | Current best understanding, not a changelog |
| Key Facts | **Structured, referenceable** | Agents can cite individual facts |
| Relations | **Typed wikilinks** | `uses`, `part-of`, `contrasts`, `evolved-from` |
| `---` separator | **Never remove** | The line between truth and evidence |
| Timeline | **Append only, never edit** | Evidence chain must be immutable |

---

## Directory Structure

```
wiki-kb/                          # Code repository
├── scripts/                      # MCP server + utilities (bind-mounted)
│   ├── wiki_mcp_server.py        # 15 MCP tools
│   ├── wiki_config.py            # Centralized configuration
│   ├── wiki_utils.py             # Frontmatter, relations, path routing
│   ├── entity_registry.py        # Entity ID + alias management
│   ├── wiki_health_monitor.py    # Health monitoring
│   ├── wiki_quality_check.py     # Page quality validation
│   ├── wiki-to-notion.py         # Optional Notion sync
│   ├── wiki-backup.sh            # Backup script
│   └── wiki-cron-wrapper.sh      # Cron job wrapper
├── tests/                        # Unit tests
├── Dockerfile
├── docker-compose.yml
├── SCHEMA.md                     # Full schema specification (v4)
└── README.md

wiki/                             # Data (bind-mounted into container)
├── concepts/                     # Default type — frameworks, analyses, ideas
├── entities/                     # Products, tools, platforms, orgs
├── people/                       # People (read-only)
├── system/                       # System pages (wiki-health)
├── raw/                          # Source materials (excluded from search)
├── logs/                         # Runtime logs
└── registry.json                 # Entity registry
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WIKI_ROOT` | No | Wiki data root inside container (default: `/data`) |
| `MCP_PORT` | No | Server port (default: `8764`) |
| `MCP_API_KEY` | No | Auth key. Unset = open access (safe for LAN) |
| `OPENVIKING_ENDPOINT` | No | Enables semantic search |
| `OPENVIKING_API_KEY` | No | OpenViking auth |
| `NOTION_API_KEY` | No | Enables Notion sync |

---

## What We Learned (Design Rationale)

### Why 3 types instead of 9

We started with 9 types (concept, entity, person, project, meeting, idea, comparison, query, tool). The agent constantly misrouted: is a "product comparison" a `comparison` or a `concept`? Is a "project update" a `project` or `entity`? Every ambiguous page was a classification error.

3 types eliminate ambiguity: things you can register in the entity registry → `entity`. People → `person`. Everything else → `concept`. Classification accuracy went from ~70% to ~95%.

### Why we killed Dream Cycle

Dream Cycle was supposed to be the killer feature — an LLM that audits all pages nightly, detects contradictions, fills gaps. In practice, the cron job either didn't run (dead pipeline) or produced surface-level feedback ("this page could use more detail"). The real quality improvement came from `wiki_review` — targeted, on-demand quality checks with concrete pass/fail criteria.

### Why no cron pipeline

We had 7 cron jobs. After weeks, only 2 had ever started. The agent triggers wiki operations on demand during conversations — that's when context is available and decisions are meaningful. Scheduled automation without context produces noise.

### Why data stays local

We initially pushed wiki data to a private GitHub repo for "backup." But the NAS has RAID, the data contains personal information, and adding a remote just meant another thing to maintain. Git history on the NAS provides rollback. That's enough.

### Why MCP and not a REST API

MCP makes knowledge a first-class tool in the agent's tool belt — not an external service it has to "call." The agent uses `wiki_search` the same way it uses `web_search` or `file_edit`. This changes the mental model from "query a database" to "use knowledge as a tool."

---

## Inspired By

- **[Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)** — The original insight: compile knowledge at write-time, not retrieve at query-time
- **[Garry Tan's gbrain](https://github.com/garrytan/gbrain)** — Compiled Truth + Timeline pattern, Dream Cycle automation, production-hardened at scale (10,000+ pages)
- **[LLM Wiki v2](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)** — Memory lifecycle, knowledge graph, hybrid search extensions
- **[元Skill方法论](https://mp.weixin.qq.com/s/AZ_DFAFf-J7V6MUcH77iLA)** — "好东西 = 总结出来 ≠ 设计出来" — knowledge compounds through summarization, not design

---

## License

MIT
