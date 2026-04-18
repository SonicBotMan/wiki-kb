#!/usr/bin/env python3
"""
wiki-to-notion.py — Sync LLM Wiki pages to Notion.

Storage architecture:
  NAS (~/wiki/) — authoritative storage
  Notion — read-only mirror for browsing

Usage:
  python wiki-to-notion.py --sync [--dry-run] [--force] [--delete]
  python wiki-to-notion.py --all [--dry-run] [--force]
  python wiki-to-notion.py --type tool|concept|entity|... [--dry-run]
  python wiki-to-notion.py --file path.md [--dry-run]
  python wiki-to-notion.py --list

Modes:
  --sync        Full pipeline: local wiki → Notion (with --delete by default)
  --all         Sync all wiki pages to Notion
  --type TYPE   Sync all pages from a specific directory
  --file PATH   Sync a single specific wiki page
  --dry-run     Show what would be synced without actually doing it
  --list        List sync status of all wiki pages
  --force       Force re-sync all pages (ignore content hash)
  --delete      Archive Notion pages whose local wiki file has been deleted
"""

import argparse
import os
import re
import sys
import json
import time
import yaml
import hashlib
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

# Load environment variables from ~/.hermes/.env if it exists
_dotenv_path = Path(os.environ.get("ENV_FILE", str(Path(os.path.expanduser("~/.hermes/.env")))))
if _dotenv_path.exists():
    for line in _dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and not line.startswith("export "):
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = value


# --- Config ---
WIKI_DIR = Path(os.environ.get("WIKI_ROOT", str(Path(os.path.expanduser("~/wiki")))))
NOTION_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# Databases that have a "Type" select property (for storing frontmatter type)
# Only set Type property for these databases to avoid 400 errors
DBS_WITH_TYPE_PROP = {"entity"}

# Directory name → Notion Database ID mapping
# Each wiki subdirectory maps to a dedicated Notion database.
# Set IDs via env vars or edit here directly.
TYPE_TO_DB = {
    "entity": os.environ.get("NOTION_DB_ENTITY", ""),
    "concept": os.environ.get("NOTION_DB_CONCEPT", ""),
    "comparison": os.environ.get("NOTION_DB_COMPARISON", ""),
    "query": os.environ.get("NOTION_DB_QUERY", ""),
    "tool": os.environ.get("NOTION_DB_TOOL", ""),
    "project": os.environ.get("NOTION_DB_PROJECT", ""),
    "person": os.environ.get("NOTION_DB_PERSON", ""),
    "meeting": os.environ.get("NOTION_DB_MEETING", ""),
    "idea": os.environ.get("NOTION_DB_IDEA", ""),
    "guide": os.environ.get("NOTION_DB_GUIDE", ""),
}

# Wiki subdirectory → type name mapping (used for routing pages to databases)
DIR_TO_TYPE = {
    "entities": "entity",
    "concepts": "concept",
    "comparisons": "comparison",
    "queries": "query",
    "tools": "tool",
    "projects": "project",
    "people": "person",
    "meetings": "meeting",
    "ideas": "idea",
}

# System directories to skip during scanning
EXCLUDE_DIRS = {"dream-reports", "raw", "src", "logs", "scripts", ".git",
                "assets", "papers", "transcripts", "articles",
                "images", "pdfs", "videos", "audio"}
EXCLUDE_FILES = {"SCHEMA.md", "RESOLVER.md", "index.md", "log.md"}

# --- Helpers ---

_NOTION_SESSION = None

def _get_session():
    global _NOTION_SESSION
    if _NOTION_SESSION is None:
        _NOTION_SESSION = requests.Session()
        _NOTION_SESSION.headers.update(HEADERS)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=10, max_retries=0
        )
        _NOTION_SESSION.mount("https://", adapter)
    return _NOTION_SESSION


def notion_request(method, path, data=None, retries=5):
    """Make a Notion API request with exponential backoff retry."""
    url = f"{API_BASE}{path}"
    session = _get_session()
    for attempt in range(retries):
        try:
            resp = session.request(method, url, json=data, timeout=30)
            if resp.status_code in (200, 201):
                return resp.json()
            if resp.status_code == 409:
                time.sleep(1)
                continue
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            print(f"  API error {resp.status_code}: {resp.text[:200]}")
            return None
        except Exception as e:
            if attempt < retries - 1:
                wait = min(2 ** attempt, 16)
                print(f"  Request error (attempt {attempt+1}/{retries}): {e}")
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
                if "SSL" in str(e) or "ssl" in str(e).lower():
                    session.close()
                    _NOTION_SESSION = None
            else:
                print(f"  Request failed after {retries} attempts: {e}")
    return None


def parse_frontmatter(content):
    """Extract YAML frontmatter and body from markdown."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, parts[2].strip()

def file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    for chunk in path.open("rb"):
        h.update(chunk)
    return h.hexdigest()

def parse_inline(text: str) -> list:
    """Convert a markdown line into Notion rich_text segments.

    Handles: **bold**, *italic*, ~~strikethrough~~, `code`, [text](url), and nesting.
    Plain text between matches is also emitted as a segment.
    """
    segments = []
    pattern = re.compile(
        r'\[([^\]]+)\]\(([^)]+)\)'   # [text](url)
        r'|\*\*(.+?)\*\*'            # **bold**
        r'|\*(.+?)\*'                # *italic*
        r'|~~(.+?)~~'                # ~~strikethrough~~
        r'|`([^`]+)`'                # `code`
    )

    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            plain = text[pos:m.start()]
            if plain:
                segments.append({"type": "text", "text": {"content": plain}})

        if m.group(1) is not None:
            link_text = m.group(1)
            link_url = m.group(2)
            inner = parse_inline(link_text)
            for seg in inner:
                seg["href"] = link_url
            segments.extend(inner)
        elif m.group(3) is not None:
            inner = parse_inline(m.group(3))
            for seg in inner:
                seg.setdefault("annotations", {})["bold"] = True
            segments.extend(inner)
        elif m.group(4) is not None:
            inner = parse_inline(m.group(4))
            for seg in inner:
                seg.setdefault("annotations", {})["italic"] = True
            segments.extend(inner)
        elif m.group(5) is not None:
            inner = parse_inline(m.group(5))
            for seg in inner:
                seg.setdefault("annotations", {})["strikethrough"] = True
            segments.extend(inner)
        elif m.group(6) is not None:
            segments.append({
                "type": "text",
                "text": {"content": m.group(6)},
                "annotations": {"code": True}
            })
        pos = m.end()

    if pos < len(text):
        trailing = text[pos:]
        if trailing:
            segments.append({"type": "text", "text": {"content": trailing}})

    if not segments:
        segments.append({"type": "text", "text": {"content": ""}})

    for seg in segments:
        content = seg.get("text", {}).get("content", "")
        if len(content) > 2000:
            seg["text"]["content"] = content[:2000]

    return segments


def _make_rich_text(text: str) -> list:
    """Shorthand: parse inline markdown into Notion rich_text."""
    text = re.sub(r"\[\[(.+?)\]\]", r"**\1**", text)
    return parse_inline(text)

def _is_para_line(s: str) -> bool:
    """Return True if a stripped line is a continuation of a paragraph."""
    if not s:
        return False
    if s.startswith("#"):
        return False
    if s.startswith("- ") or s.startswith("* "):
        return False
    if re.match(r"^\d+\.", s):
        return False
    if s in ("---", "***", "___"):
        return False
    if s.startswith("```"):
        return False
    if "|" in s:
        return False
    if s.startswith(">"):
        return False
    if re.match(r"^!\[", s):
        return False
    return True

_NOTION_LANGUAGES = {
    "abap", "arduino", "c", "clojure", "coffeescript", "c++", "c#", "css", "dart",
    "docker", "elixir", "elm", "erlang", "flow", "fortran", "f#", "gherkin", "git",
    "glsl", "go", "graphql", "groovy", "haskell", "html", "java", "javascript", "json",
    "julia", "kotlin", "latex", "less", "lisp", "livescript", "lua", "makefile", "markdown",
    "mathematica", "objective-c", "ocaml", "pascal", "perl", "php", "powershell", "python",
    "r", "ruby", "rust", "sass", "scala", "scheme", "scss", "shell", "sql", "swift",
    "text", "typescript", "vb.net", "verilog", "vhdl", "visual basic", "webassembly",
    "xml", "yaml", "plain text",
}

# Common language aliases that Notion doesn't support directly
_LANGUAGE_ALIASES = {
    "sh": "shell", "bash": "shell", "zsh": "shell", "fish": "shell",
    "js": "javascript", "jsx": "javascript", "tsx": "typescript", "ts": "typescript",
    "py": "python", "rb": "ruby", "rs": "rust", "yml": "yaml",
    "toml": "yaml", "ini": "plain text", "conf": "plain text", "cfg": "plain text",
    "dockerfile": "docker", "diff": "plain text", "console": "plain text",
    "typst": "plain text", "typ": "plain text",
    "c++": "c++", "cpp": "c++", "cc": "c++",
    "c#": "c#", "cs": "c#",
    "f#": "f#", "fs": "f#",
    "objc": "objective-c",
    "make": "makefile",
}

def _normalize_code_language(lang: str) -> str:
    """Normalize a code language identifier to one Notion supports."""
    if not lang:
        return "plain text"
    lang_lower = lang.strip().lower()
    # Check alias first
    if lang_lower in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[lang_lower]
    # Check direct match
    if lang_lower in _NOTION_LANGUAGES:
        return lang_lower
    # Fallback
    return "plain text"

def markdown_to_notion_blocks(md_text):
    """Convert markdown text to Notion blocks (simplified)."""
    blocks = []
    lines = md_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        # Headings
        heading_match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            block_type = f"heading_{level}"
            blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {"rich_text": _make_rich_text(text)}
            })
            i += 1
            continue

        # Bullet lists
        if line.startswith("- ") or line.startswith("* "):
            text = line[2:]
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": _make_rich_text(text)}
            })
            i += 1
            continue

        # Numbered lists
        num_match = re.match(r"^\d+\.\s+(.+)$", line)
        if num_match:
            text = num_match.group(1)
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": _make_rich_text(text)}
            })
            i += 1
            continue

        # Horizontal rule
        if line in ("---", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # Code blocks
        if line.startswith("```"):
            lang = line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            code_text = "\n".join(code_lines)
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": code_text[:2000]}}],
                    "language": _normalize_code_language(lang)
                }
            })
            i += 1
            continue

        # Image: ![alt](url)
        img_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)$', line.strip())
        if img_match:
            alt_text = img_match.group(1)
            img_url = img_match.group(2)
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": img_url},
                    "caption": [{"type": "text", "text": {"content": alt_text}}] if alt_text else []
                }
            })
            i += 1
            continue

        # Blockquote
        if line.startswith(">"):
            quote_parts = [line[1:].strip()]
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                quote_parts.append(lines[i][1:].strip())
                i += 1
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": _make_rich_text(" ".join(quote_parts))}
            })
            continue

        # Table
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            table_rows = []
            header_cells = [c.strip() for c in line.split("|")[1:-1]]
            table_rows.append(header_cells)
            i += 2
            while i < len(lines) and "|" in lines[i]:
                cells = [c.strip() for c in lines[i].split("|")[1:-1]]
                table_rows.append(cells)
                i += 1
            if table_rows:
                blocks.append({
                    "object": "block",
                    "type": "table",
                    "table": {
                        "table_width": max(len(r) for r in table_rows),
                        "has_column_header": True,
                        "has_row_header": False,
                        "children": [
                            {
                                "object": "block",
                                "type": "table_row",
                                "table_row": {
                                    "cells": [
                                        _make_rich_text(cell) for cell in row
                                    ]
                                }
                            }
                            for row in table_rows
                        ]
                    }
                })
            continue

        # Regular paragraph
        para_lines = [line]
        i += 1
        while i < len(lines) and _is_para_line(lines[i].strip()):
            para_lines.append(lines[i].strip())
            i += 1

        para_text = " ".join(para_lines)
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _make_rich_text(para_text)}
        })

    return blocks[:100]

def get_existing_pages(db_id, dedup=True):
    """Get all existing pages in a Notion database (paginated).

    Returns a dict keyed by Wiki File path, each value containing:
      page_id, name, wiki_file, content_hash (or empty string if unset).

    When dedup=True, detects duplicate entries (same wiki_file) and
    archives all but the most recently updated one.
    """
    all_entries = []  # list of (wiki_file, page_info, last_edited_time)
    has_more = True
    cursor = None
    while has_more:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"/databases/{db_id}/query", body)
        if not result:
            break
        for page in result.get("results", []):
            if page.get("archived", False):
                continue
            name = ""
            title_prop = page.get("properties", {}).get("Name", {})
            if title_prop.get("title"):
                name = title_prop["title"][0]["plain_text"] if title_prop["title"] else ""
            wiki_file = ""
            wf_prop = page.get("properties", {}).get("Wiki File", {})
            if wf_prop.get("rich_text"):
                wiki_file = wf_prop["rich_text"][0]["plain_text"] if wf_prop["rich_text"] else ""
            content_hash = ""
            ch_prop = page.get("properties", {}).get("Content Hash", {})
            if ch_prop.get("rich_text"):
                content_hash = ch_prop["rich_text"][0]["plain_text"] if ch_prop["rich_text"] else ""
            page_info = {
                "page_id": page["id"],
                "name": name,
                "wiki_file": wiki_file,
                "content_hash": content_hash,
            }
            last_edited = page.get("last_edited_time", "")
            all_entries.append((wiki_file, page_info, last_edited))
        has_more = result.get("has_more", False)
        cursor = result.get("next_cursor")
        time.sleep(0.4)

    # Dedup: keep the most recently edited entry per wiki_file, archive extras
    pages = {}
    if dedup:
        from collections import defaultdict
        groups = defaultdict(list)
        for wiki_file, page_info, last_edited in all_entries:
            groups[wiki_file].append((page_info, last_edited))

        for wiki_file, entries in groups.items():
            if len(entries) > 1:
                # Sort by last_edited_time descending, keep newest
                entries.sort(key=lambda x: x[1] or "", reverse=True)
                keep = entries[0][0]
                extras = entries[1:]
                print(f"  ⚠ Dedup: {wiki_file} has {len(entries)} entries, archiving {len(extras)} duplicate(s)")
                for extra_info, _ in extras:
                    result = notion_request("PATCH", f"/pages/{extra_info['page_id']}", {"archived": True})
                    if result is not None:
                        print(f"    Archived duplicate: {extra_info['name']} ({extra_info['page_id'][:12]})")
                    else:
                        print(f"    Failed to archive: {extra_info['name']}")
                    time.sleep(0.4)
                pages[wiki_file] = keep
            else:
                pages[wiki_file] = entries[0][0]
    else:
        for wiki_file, page_info, _ in all_entries:
            pages[wiki_file] = page_info

    return pages

def sync_page(page_path, db_id, dry_run=False):
    """Sync a single wiki page to Notion."""
    content = page_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)

    title = fm.get("title", page_path.stem)
    page_type = fm.get("type", "")
    tags = fm.get("tags", [])
    created = fm.get("created", "")
    updated = fm.get("updated", "")
    sources = fm.get("sources", [])
    sources_str = ", ".join(sources) if isinstance(sources, list) else str(sources)
    wiki_file = str(page_path.relative_to(WIKI_DIR))
    content_hash = file_hash(page_path)

    if dry_run:
        print(f"  [DRY] Would create: {title} in {page_type} db")
        print(f"         Wiki File: {wiki_file}")
        print(f"         Tags: {tags}, Hash: {content_hash[:16]}...")
        return True

    # Build properties
    properties = {
        "Name": {"title": [{"type": "text", "text": {"content": title[:100]}}]},
        "Tags": {"multi_select": [{"name": t} for t in (tags or [])]},
        "Wiki File": {"rich_text": [{"type": "text", "text": {"content": wiki_file}}]},
        "Content Hash": {"rich_text": [{"type": "text", "text": {"content": content_hash}}]},
    }

    if created:
        properties["Created"] = {"date": {"start": str(created)}}
    if updated:
        properties["Updated"] = {"date": {"start": str(updated)}}
    if sources_str:
        properties["Sources"] = {"rich_text": [{"type": "text", "text": {"content": sources_str[:2000]}}]}
    # Store the frontmatter type as a select property (only for databases that support it)
    notion_type = ""
    for t, db_id_t in TYPE_TO_DB.items():
        if db_id == db_id_t:
            notion_type = t
            break
    if page_type and notion_type in DBS_WITH_TYPE_PROP:
        properties["Type"] = {"select": {"name": page_type}}

    blocks = markdown_to_notion_blocks(body)
    if not blocks:
        blocks = [{"object": "block", "type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": "(empty page)"}}]
        }}]

    data = {
        "parent": {"database_id": db_id},
        "properties": properties,
        "children": blocks,
    }

    result = notion_request("POST", "/pages", data)
    if result:
        notion_id = result.get("id", "")
        url = result.get("url", "")
        print(f"  Created: {title}")
        print(f"    Notion: {url}")
        print(f"    Wiki File: {wiki_file}")
        return notion_id
    else:
        print(f"  Failed: {title}")
        return None

def update_page(page_path, notion_page_id, db_id, dry_run=False):
    """Update an existing Notion page with new wiki content.

    Uses a safe atomic approach:
      1. Fetch existing block IDs
      2. Append new blocks to the page
      3. Only after successful append, delete old blocks
      4. Update properties last
    """
    content = page_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)

    title = fm.get("title", page_path.stem)
    tags = fm.get("tags", [])
    updated = fm.get("updated", "")
    sources = fm.get("sources", [])
    sources_str = ", ".join(sources) if isinstance(sources, list) else str(sources)
    page_type = fm.get("type", "")
    content_hash = file_hash(page_path)

    if dry_run:
        print(f"  [DRY] Would update: {title}")
        print(f"         New hash: {content_hash[:16]}...")
        return True

    # Step 1: Fetch existing block IDs (skip archived blocks)
    existing = notion_request("GET", f"/blocks/{notion_page_id}/children?page_size=100")
    old_block_ids = []
    if existing and existing.get("results"):
        old_block_ids = [block["id"] for block in existing["results"] if not block.get("archived", False)]
        while existing.get("has_more"):
            cursor = existing.get("next_cursor")
            existing = notion_request("GET", f"/blocks/{notion_page_id}/children?page_size=100&start_cursor={cursor}")
            if existing and existing.get("results"):
                old_block_ids.extend(block["id"] for block in existing["results"] if not block.get("archived", False))

    # Step 2: Append new blocks FIRST
    blocks = markdown_to_notion_blocks(body)
    if blocks:
        try:
            for i in range(0, len(blocks), 100):
                batch = blocks[i:i + 100]
                notion_request("PATCH", f"/blocks/{notion_page_id}/children", {"children": batch})
                time.sleep(0.4)
        except Exception as e:
            print(f"  ✗ Failed to append new blocks for {title}: {e}")
            return False

    # Step 3: Delete old blocks AFTER new blocks are safely appended
    if old_block_ids:
        for bid in reversed(old_block_ids):
            result = notion_request("DELETE", f"/blocks/{bid}")
            if result is None:
                pass  # Already archived or other error — skip silently
            time.sleep(0.35)

    # Step 4: Update properties LAST
    properties = {
        "Name": {"title": [{"type": "text", "text": {"content": title[:100]}}]},
        "Tags": {"multi_select": [{"name": t} for t in (tags or [])]},
        "Content Hash": {"rich_text": [{"type": "text", "text": {"content": content_hash}}]},
    }
    created = fm.get("created", "")
    if created:
        properties["Created"] = {"date": {"start": str(created)}}
    if updated:
        properties["Updated"] = {"date": {"start": str(updated)}}
    if sources_str:
        properties["Sources"] = {"rich_text": [{"type": "text", "text": {"content": sources_str[:2000]}}]}
    notion_type = ""
    for t, db_id_t in TYPE_TO_DB.items():
        if db_id == db_id_t:
            notion_type = t
            break
    if page_type and notion_type in DBS_WITH_TYPE_PROP:
        properties["Type"] = {"select": {"name": page_type}}

    notion_request("PATCH", f"/pages/{notion_page_id}", {"properties": properties})
    time.sleep(0.4)

    print(f"  ✓ Updated: {title} (hash: {content_hash[:16]}...)")
    return True

def archive_orphaned_pages(existing_pages, local_files, dry_run=False):
    """Archive Notion pages whose Wiki File points to a deleted local file."""
    archived = 0
    for wiki_file, page_info in existing_pages.items():
        if not wiki_file:
            continue
        if wiki_file not in local_files:
            page_id = page_info["page_id"]
            name = page_info.get("name", "(untitled)")
            if dry_run:
                print(f"  [DRY] Would archive: {name} (Wiki File: {wiki_file})")
            else:
                result = notion_request("PATCH", f"/pages/{page_id}", {"archived": True})
                if result is not None:
                    print(f"  Archived: {name} (Wiki File: {wiki_file})")
                else:
                    print(f"  Failed to archive: {name} (Wiki File: {wiki_file})")
            archived += 1
            time.sleep(0.4)
    return archived

def collect_wiki_pages(wiki_type=None):
    """Collect all wiki pages, optionally filtered by type (directory name).

    Scans:
      - All subdirectories in DIR_TO_TYPE (entities/, concepts/, tools/, etc.)
      - Wiki root files with valid frontmatter (brain-upgrade-plan.md, etc.)
    """
    pages = []
    dirs_to_scan = []

    if wiki_type:
        for d, t in DIR_TO_TYPE.items():
            if t == wiki_type:
                dirs_to_scan.append(d)
    else:
        dirs_to_scan = list(DIR_TO_TYPE.keys())

    # Scan subdirectories
    for d in dirs_to_scan:
        dir_path = WIKI_DIR / d
        if dir_path.exists():
            for f in sorted(dir_path.glob("*.md")):
                pages.append((f, DIR_TO_TYPE[d]))

    # Scan wiki root for pages with valid frontmatter type
    if not wiki_type:
        for f in sorted(WIKI_DIR.glob("*.md")):
            if f.name in EXCLUDE_FILES:
                continue
            try:
                content = f.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                page_type = fm.get("type", "")
                if page_type:
                    pages.append((f, page_type))
            except Exception:
                continue

    return pages

def main():
    parser = argparse.ArgumentParser(description="Sync LLM Wiki → Notion")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Sync all pages to Notion")
    group.add_argument("--type", choices=list(DIR_TO_TYPE.values()),
                       help="Sync pages of a specific type (entity/concept/tool/project/...)")
    group.add_argument("--file", help="Sync a single file")
    group.add_argument("--list", action="store_true", help="List sync status")
    group.add_argument("--sync", action="store_true",
                       help="Full sync: local wiki → Notion (implies --delete)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without syncing")
    parser.add_argument("--force", action="store_true",
                        help="Force re-sync all pages (ignore content hash)")
    parser.add_argument("--delete", action="store_true",
                        help="Archive Notion pages whose local wiki file has been deleted")
    args = parser.parse_args()

    # --sync implies --all and --delete
    if args.sync:
        args.all = True
        args.delete = True

    if not NOTION_KEY and not args.list:
        print("ERROR: NOTION_API_KEY not set. Add it to ~/.hermes/.env or wiki/.env")
        sys.exit(1)

    # Validate database IDs for target types
    target_types = set()
    if args.sync or args.all:
        target_types = set(DIR_TO_TYPE.values())
        # Also include types found in wiki root pages
        for f in sorted(WIKI_DIR.glob("*.md")):
            if f.name in EXCLUDE_FILES:
                continue
            try:
                content = f.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                pt = fm.get("type", "")
                if pt:
                    target_types.add(pt)
            except Exception:
                continue
    elif args.type:
        target_types = {args.type}
    elif args.file:
        fpath = Path(args.file).expanduser().resolve()
        for d, t in DIR_TO_TYPE.items():
            if d in str(fpath):
                target_types.add(t)
                break
        if not target_types:
            # Try to read frontmatter type for root files
            try:
                content = fpath.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                pt = fm.get("type", "")
                if pt:
                    target_types.add(pt)
            except Exception:
                pass

    # Check for missing database IDs
    missing_dbs = [t for t in target_types if not TYPE_TO_DB.get(t)]
    if missing_dbs and not args.list:
        print(f"ERROR: Missing Notion database IDs for types: {', '.join(missing_dbs)}")
        print("Set them via environment variables (e.g., NOTION_DB_TOOL=xxx) or edit TYPE_TO_DB.")
        sys.exit(1)

    # --list: show sync status
    if args.list:
        print("Wiki → Notion sync status:\n")
        all_pages = collect_wiki_pages()
        existing_cache = {}
        for f, ptype in all_pages:
            db_id = TYPE_TO_DB.get(ptype)
            if not db_id:
                print(f"  ⚠ {f.relative_to(WIKI_DIR)} -> NO DATABASE for type '{ptype}'")
                continue
            if db_id not in existing_cache:
                existing_cache[db_id] = get_existing_pages(db_id)
            existing = existing_cache[db_id]
            rel_path = str(f.relative_to(WIKI_DIR))
            if rel_path in existing:
                ep = existing[rel_path]
                h = ep.get("content_hash", "")
                h_display = h[:12] + "..." if h else "no hash"
                print(f"  ✓ {rel_path} -> {ep['name']} (hash: {h_display})")
            else:
                print(f"  ○ {rel_path} -> NOT SYNCED")
        print(f"\nTotal wiki pages: {len(all_pages)}")
        return

    # --file: sync a single file
    if args.file:
        fpath = Path(args.file).expanduser().resolve()
        if not fpath.exists():
            print(f"ERROR: File not found: {fpath}")
            sys.exit(1)
        ptype = None
        # Check if file is in a known subdirectory
        for d, t in DIR_TO_TYPE.items():
            if d in str(fpath):
                ptype = t
                break
        # If not in subdirectory, use frontmatter type
        if not ptype:
            try:
                content = fpath.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                ptype = fm.get("type", "")
            except Exception:
                pass
        if not ptype:
            print(f"ERROR: Cannot determine page type for: {fpath}")
            sys.exit(1)
        db_id = TYPE_TO_DB.get(ptype)
        if not db_id:
            print(f"ERROR: No Notion database configured for type '{ptype}'")
            sys.exit(1)
        print(f"Syncing single file: {fpath.name} ({ptype})")

        existing = get_existing_pages(db_id)
        rel_path = str(fpath.relative_to(WIKI_DIR))
        local_hash = file_hash(fpath)

        if rel_path in existing:
            ep = existing[rel_path]
            notion_hash = ep.get("content_hash", "")
            if local_hash == notion_hash and not args.force:
                print(f"  Skipped (up-to-date): {fpath.name}")
            else:
                print(f"  Updating (hash changed): {fpath.name}")
                update_page(fpath, ep["page_id"], db_id, dry_run=args.dry_run)
        else:
            sync_page(fpath, db_id, dry_run=args.dry_run)
        return

    # --all or --sync or --type: sync all pages of given type(s)
    pages = collect_wiki_pages(wiki_type=args.type)

    # Build existing_cache
    existing_cache = {}
    for t in target_types:
        db_id = TYPE_TO_DB.get(t)
        if db_id:
            existing_cache[db_id] = get_existing_pages(db_id)

    if not pages and not args.delete:
        print("No wiki pages found.")
        return

    if not args.dry_run:
        print(f"Found {len(pages)} wiki page(s) to sync.\n")

    created = 0
    updated = 0
    skipped = 0
    errors = 0

    for fpath, ptype in pages:
        db_id = TYPE_TO_DB.get(ptype)
        if not db_id:
            print(f"  ⚠ SKIP {fpath.name} — no database for type '{ptype}'")
            errors += 1
            continue

        if db_id not in existing_cache:
            existing_cache[db_id] = get_existing_pages(db_id)

        rel_path = str(fpath.relative_to(WIKI_DIR))
        existing = existing_cache[db_id]
        local_hash = file_hash(fpath)

        if rel_path in existing:
            ep = existing[rel_path]
            notion_hash = ep.get("content_hash", "")

            if local_hash != notion_hash or args.force:
                action = "FORCE" if args.force and local_hash == notion_hash else "UPDATE"
                print(f"[{action}] {fpath.name}")
                ok = update_page(fpath, ep["page_id"], db_id, dry_run=args.dry_run)
                if ok or args.dry_run:
                    updated += 1
                else:
                    errors += 1
            else:
                skipped += 1
        else:
            print(f"[CREATE] {fpath.name}")
            ok = sync_page(fpath, db_id, dry_run=args.dry_run)
            if ok or args.dry_run:
                created += 1
            else:
                errors += 1

        time.sleep(0.4)

    print(f"\nDone. Created: {created}, Updated: {updated}, "
          f"Skipped (up-to-date): {skipped}, Errors: {errors}")

    # --delete: archive orphaned Notion pages
    if args.delete:
        print("\n--delete: checking for orphaned Notion pages...")
        local_rel_paths = set()
        for fpath, _ptype in pages:
            local_rel_paths.add(str(fpath.relative_to(WIKI_DIR)))

        all_archived = 0
        for db_id, existing in existing_cache.items():
            n = archive_orphaned_pages(existing, local_rel_paths, dry_run=args.dry_run)
            all_archived += n

        if all_archived == 0:
            print("  No orphaned pages found.")
        else:
            print(f"\n  Archived {all_archived} orphaned page(s).")


if __name__ == "__main__":
    main()
