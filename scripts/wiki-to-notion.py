#!/usr/bin/env python3
"""
wiki-to-notion.py — Sync LLM Wiki pages between Local ↔ NAS (WebDAV) ↔ Notion.

Storage architecture:
  NAS WebDAV (<NAS_WEBDAV_URL>/) — authoritative storage
  Local (~/wiki/) — working copy for fast read/write
  Notion — read-only mirror for browsing

Usage:
  python wiki-to-notion.py [--all] [--type entity|concept|comparison|query] [--file path.md] [--dry-run]
  python wiki-to-notion.py --sync          # Full sync: local wiki → Notion
  python wiki-to-notion.py --list          # List sync status

Modes:
  --all         Sync all wiki pages to Notion (check Wiki File property)
  --type TYPE   Sync all pages of a specific type
  --file PATH   Sync a single specific wiki page
  --dry-run     Show what would be synced without actually doing it
  --list        List sync status of all wiki pages
  --sync        Full pipeline: local wiki → Notion (no NAS)
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
            # Only set if not already in environment
            if key not in os.environ:
                os.environ[key] = value


# --- Config ---
WIKI_DIR = Path(os.environ.get("WIKI_ROOT", str(Path(os.path.expanduser("~/wiki")))))
NOTION_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"

# NAS WebDAV config — read from env vars
WEBDAV_BASE = os.environ.get("WEBDAV_BASE_URL", "")
WEBDAV_USER = os.environ.get("WEBDAV_USER", "")
WEBDAV_PASS = os.environ.get("WEBDAV_PASS", "")

HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

# Sync state cache path
SYNC_STATE_PATH = Path(os.path.expanduser("~/.wiki-sync.json"))

# --- Sync state cache ---

def load_sync_state() -> dict:
    """Load the sync state cache from disk."""
    if SYNC_STATE_PATH.exists():
        try:
            return json.loads(SYNC_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_sync_state(state: dict) -> None:
    """Persist the sync state cache to disk."""
    try:
        SYNC_STATE_PATH.write_text(json.dumps(state, indent=2))
    except OSError as e:
        print(f"  WARNING: Could not save sync state: {e}")

# --- Hashing helper ---

def file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    for chunk in path.open("rb"):
        h.update(chunk)
    return h.hexdigest()

# --- WebDAV helpers ---

def webdav_head(remote_rel: str) -> dict | None:
    """HEAD request to get ETag and Last-Modified for a remote file.
    Returns dict with 'etag' and 'last_modified' keys, or None if not found/error."""
    url = f"{WEBDAV_BASE}/{remote_rel}"
    try:
        resp = requests.head(url, auth=(WEBDAV_USER, WEBDAV_PASS), timeout=15)
        if resp.status_code in (200, 204):
            return {
                "etag": resp.headers.get("ETag", ""),
                "last_modified": resp.headers.get("Last-Modified", ""),
            }
        return None
    except Exception:
        return None

def webdav_upload(local_path: Path, remote_rel: str) -> bool:
    """Upload a file to NAS WebDAV. remote_rel is relative to /wiki/."""
    url = f"{WEBDAV_BASE}/{remote_rel}"
    try:
        resp = requests.put(url, data=local_path.read_bytes(),
                          auth=(WEBDAV_USER, WEBDAV_PASS), timeout=30)
        return resp.status_code in (200, 201, 204)
    except Exception as e:
        print(f"  WebDAV upload error: {e}")
        return False

def webdav_download(remote_rel: str, local_path: Path) -> bool:
    """Download a file from NAS WebDAV."""
    url = f"{WEBDAV_BASE}/{remote_rel}"
    try:
        resp = requests.get(url, auth=(WEBDAV_USER, WEBDAV_PASS), timeout=30)
        if resp.status_code == 200:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(resp.content)
            return True
        return False
    except Exception as e:
        print(f"  WebDAV download error: {e}")
        return False

def webdav_list(remote_dir: str = "", with_meta: bool = False) -> dict | list:
    """List all files under a remote directory (recursive).
    with_meta=True: returns {rel_path: {"etag": ..., "last_modified": ..., "size": ...}}
    with_meta=False: returns [rel_path, ...] (backward compat)
    """
    import xml.etree.ElementTree as ET

    url = f"{WEBDAV_BASE}/{remote_dir}"
    try:
        resp = requests.request("PROPFIND", url,
                              auth=(WEBDAV_USER, WEBDAV_PASS),
                              headers={"Depth": "infinity"},
                              timeout=30)
        if resp.status_code != 207:
            return {} if with_meta else []
        root = ET.fromstring(resp.content)
        ns = {"d": "DAV:"}
        prefix = "/wiki/"

        if with_meta:
            result = {}
            for response in root.findall("d:response", ns):
                href_elem = response.find("d:href", ns)
                if href_elem is None:
                    continue
                href = (href_elem.text or "").rstrip("/")
                if not href or href == prefix.rstrip("/"):
                    continue
                # Remove /wiki/ prefix
                if href.startswith(prefix):
                    rel = href[len(prefix):]
                else:
                    rel = href
                if not rel:
                    continue
                # Extract metadata from propstat
                etag = ""
                last_modified = ""
                size = 0
                for propstat in response.findall("d:propstat/d:prop", ns):
                    e = propstat.find("d:getetag", ns)
                    if e is not None and e.text:
                        etag = e.text.strip('"')
                    lm = propstat.find("d:getlastmodified", ns)
                    if lm is not None and lm.text:
                        last_modified = lm.text
                    s = propstat.find("d:getcontentlength", ns)
                    if s is not None and s.text:
                        try:
                            size = int(s.text)
                        except ValueError:
                            pass
                result[rel] = {
                    "etag": etag,
                    "last_modified": last_modified,
                    "size": size,
                }
            return result
        else:
            files = []
            for href_elem in root.findall(".//d:href", ns):
                href = href_elem.text or ""
                if href.startswith(prefix):
                    href = href[len(prefix):]
                if href and not href.endswith("/"):
                    files.append(href)
            return files
    except Exception as e:
        print(f"  WebDAV list error: {e}")
        return {} if with_meta else []

def webdav_delete(remote_rel: str) -> bool:
    """Delete a file from NAS WebDAV."""
    url = f"{WEBDAV_BASE}/{remote_rel}"
    try:
        resp = requests.delete(url, auth=(WEBDAV_USER, WEBDAV_PASS), timeout=30)
        return resp.status_code in (200, 204)
    except Exception:
        return False

# Type → Notion Database ID mapping
# Set via environment variable or .env file
TYPE_TO_DB = {
    "entity": os.environ.get("NOTION_DB_ENTITY", ""),
    "concept": os.environ.get("NOTION_DB_CONCEPT", ""),
    "comparison": os.environ.get("NOTION_DB_COMPARISON", ""),
    "query": os.environ.get("NOTION_DB_QUERY", ""),
}

# Wiki dirs → type mapping
DIR_TO_TYPE = {
    "entities": "entity",
    "concepts": "concept",
    "comparisons": "comparison",
    "queries": "query",
}

# --- Helpers ---

# Shared session for connection pooling (avoids SSL handshake per request)
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
            if resp.status_code == 200 or resp.status_code == 201:
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
                wait = min(2 ** attempt, 16)  # 1, 2, 4, 8, 16s exponential backoff
                print(f"  Request error (attempt {attempt+1}/{retries}): {e}")
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
                # Close and recreate session on SSL errors to reset connection pool
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

def parse_inline(text: str) -> list:
    """Convert a markdown line into Notion rich_text segments.

    Handles: **bold**, *italic*, ~~strikethrough~~, `code`, [text](url), and nesting.
    Plain text between matches is also emitted as a segment.
    """
    segments = []

    # Combined regex for all inline patterns.
    # Order matters: links first (they contain brackets), then bold, italic, strikethrough, code.
    # Group 1 = link text, Group 2 = link url  OR
    # Group 3 = bold content  OR
    # Group 4 = italic content  OR
    # Group 5 = strikethrough content  OR
    # Group 6 = inline code content
    pattern = re.compile(
        r'\[([^\]]+)\]\(([^)]+)\)'   # [text](url)
        r'|\*\*(.+?)\*\*'            # **bold**
        r'|\*(.+?)\*'                # *italic*
        r'|~~(.+?)~~'                # ~~strikethrough~~
        r'|`([^`]+)`'                # `code`
    )

    pos = 0
    for m in pattern.finditer(text):
        # Emit any plain text before this match
        if m.start() > pos:
            plain = text[pos:m.start()]
            if plain:
                segments.append({"type": "text", "text": {"content": plain}})

        if m.group(1) is not None:
            # Link: [text](url)
            link_text = m.group(1)
            link_url = m.group(2)
            # Recursively parse the link text for nested formatting
            inner = parse_inline(link_text)
            for seg in inner:
                seg["href"] = link_url
            segments.extend(inner)
        elif m.group(3) is not None:
            # Bold: **content**
            inner = parse_inline(m.group(3))
            for seg in inner:
                seg.setdefault("annotations", {})["bold"] = True
            segments.extend(inner)
        elif m.group(4) is not None:
            # Italic: *content*
            inner = parse_inline(m.group(4))
            for seg in inner:
                seg.setdefault("annotations", {})["italic"] = True
            segments.extend(inner)
        elif m.group(5) is not None:
            # Strikethrough: ~~content~~
            inner = parse_inline(m.group(5))
            for seg in inner:
                seg.setdefault("annotations", {})["strikethrough"] = True
            segments.extend(inner)
        elif m.group(6) is not None:
            # Inline code: `content`
            segments.append({
                "type": "text",
                "text": {"content": m.group(6)},
                "annotations": {"code": True}
            })

        pos = m.end()

    # Trailing plain text
    if pos < len(text):
        trailing = text[pos:]
        if trailing:
            segments.append({"type": "text", "text": {"content": trailing}})

    # Ensure we always return at least one segment (empty text) so Notion doesn't choke
    if not segments:
        segments.append({"type": "text", "text": {"content": ""}})

    # Notion rich_text items must have content <= 2000 chars each.
    # If a single segment exceeds that, truncate it.
    for seg in segments:
        content = seg.get("text", {}).get("content", "")
        if len(content) > 2000:
            seg["text"]["content"] = content[:2000]

    return segments


def _make_rich_text(text: str) -> list:
    """Shorthand: parse inline markdown into Notion rich_text, joining to single segment if plain."""
    # Pre-process [[wikilinks]] → **bold**
    text = re.sub(r"\[\[(.+?)\]\]", r"**\1**", text)
    return parse_inline(text)

def _is_para_line(s: str) -> bool:
    """Return True if a stripped line is a continuation of a paragraph (not a block start)."""
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

def markdown_to_notion_blocks(md_text):
    """Convert markdown text to Notion blocks (simplified)."""
    blocks = []
    lines = md_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines
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
                    "language": lang or "plain text"
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

        # Blockquote — merge consecutive > lines into a single quote block
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

        # Table (simple: first row is header, then separator, then data rows)
        if "|" in line and i + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip()):
            table_rows = []
            # Parse the FIRST row as the header row
            header_cells = [c.strip() for c in line.split("|")[1:-1]]
            table_rows.append(header_cells)
            i += 2  # skip header line + separator line
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

        # Regular paragraph — collect consecutive non-special lines
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

    # Notion limits: max 100 blocks per append
    return blocks[:100]

def get_existing_pages(db_id):
    """Get all existing pages in a Notion database (paginated).

    Returns a dict keyed by Wiki File path, each value containing:
      page_id, name, wiki_file, content_hash (or empty string if unset).
    """
    pages = {}
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
            name = ""
            title_prop = page.get("properties", {}).get("Name", {})
            if title_prop.get("title"):
                name = title_prop["title"][0]["plain_text"] if title_prop["title"] else ""
            wiki_file = ""
            wf_prop = page.get("properties", {}).get("Wiki File", {})
            if wf_prop.get("rich_text"):
                wiki_file = wf_prop["rich_text"][0]["plain_text"] if wf_prop["rich_text"] else ""
            # Extract Content Hash property
            content_hash = ""
            ch_prop = page.get("properties", {}).get("Content Hash", {})
            if ch_prop.get("rich_text"):
                content_hash = ch_prop["rich_text"][0]["plain_text"] if ch_prop["rich_text"] else ""
            pages[wiki_file] = {
                "page_id": page["id"],
                "name": name,
                "wiki_file": wiki_file,
                "content_hash": content_hash,
            }
        has_more = result.get("has_more", False)
        cursor = result.get("next_cursor")
        time.sleep(0.4)  # Rate limit
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

    # Compute content hash for change detection
    content_hash = file_hash(page_path)

    if dry_run:
        print(f"  [DRY] Would create: {title} in {page_type} db")
        print(f"         Tags: {tags}, Sources: {sources_str}")
        print(f"         Content Hash: {content_hash[:16]}...")
        return True

    # Build properties
    properties = {
        "Name": {"title": [{"type": "text", "text": {"content": title[:100]}}]},
        "Tags": {"multi_select": [{"name": t} for t in tags]},
        "Wiki File": {"rich_text": [{"type": "text", "text": {"content": wiki_file}}]},
        "Content Hash": {"rich_text": [{"type": "text", "text": {"content": content_hash}}]},
    }

    if created:
        properties["Created"] = {"date": {"start": str(created)}}
    if updated:
        properties["Updated"] = {"date": {"start": str(updated)}}
    if sources_str:
        properties["Sources"] = {"rich_text": [{"type": "text", "text": {"content": sources_str[:2000]}}]}

    # Entity-specific: Type property
    if page_type and db_id == TYPE_TO_DB.get("entity"):
        properties["Type"] = {"select": {"name": page_type}}

    # Convert body to blocks
    blocks = markdown_to_notion_blocks(body)
    if not blocks:
        blocks = [{"object": "block", "type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": "(empty page)"}}]
        }}]

    # Create page
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
        print(f"    Wiki file: {wiki_file}")
        print(f"    Hash: {content_hash[:16]}...")
        return notion_id
    else:
        print(f"  Failed: {title}")
        return None

def update_page(page_path, notion_page_id, db_id, dry_run=False):
    """Update an existing Notion page with new wiki content.

    Uses a safe atomic approach:
      1. Update page properties (including Content Hash)
      2. Fetch existing block IDs
      3. Append new blocks to the page
      4. Only after successful append, delete old blocks
    This ensures the page is never left empty if something fails.
    """
    content = page_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)

    title = fm.get("title", page_path.stem)
    tags = fm.get("tags", [])
    updated = fm.get("updated", "")
    sources = fm.get("sources", [])
    sources_str = ", ".join(sources) if isinstance(sources, list) else str(sources)
    page_type = fm.get("type", "")

    # Compute content hash for change detection
    content_hash = file_hash(page_path)

    if dry_run:
        print(f"  [DRY] Would update: {title}")
        print(f"         New hash: {content_hash[:16]}...")
        return True

    # Step 1: Fetch existing block IDs BEFORE appending new content
    existing = notion_request("GET", f"/blocks/{notion_page_id}/children?page_size=100")
    old_block_ids = []
    if existing and existing.get("results"):
        old_block_ids = [block["id"] for block in existing["results"]]
        while existing.get("has_more"):
            cursor = existing.get("next_cursor")
            existing = notion_request("GET", f"/blocks/{notion_page_id}/children?page_size=100&start_cursor={cursor}")
            if existing and existing.get("results"):
                old_block_ids.extend(block["id"] for block in existing["results"])

    # Step 2: Build and append new blocks FIRST (page keeps old content during this step)
    blocks = markdown_to_notion_blocks(body)
    if blocks:
        try:
            for i in range(0, len(blocks), 100):
                batch = blocks[i:i + 100]
                notion_request("PATCH", f"/blocks/{notion_page_id}/children", {"children": batch})
                time.sleep(0.4)
        except Exception as e:
            print(f"  ✗ Failed to append new blocks for {title}: {e}")
            print(f"    Old content preserved on page. Properties NOT updated — will retry next sync.")
            return False

    # Step 3: Delete old blocks AFTER new blocks are safely appended
    if old_block_ids:
        failed_deletes = 0
        for bid in reversed(old_block_ids):
            result = notion_request("DELETE", f"/blocks/{bid}")
            if result is None:
                failed_deletes += 1
            time.sleep(0.35)  # Respect rate limits (3 req/s)

    # Step 4: Update properties ONLY AFTER content is fully replaced
    properties = {
        "Name": {"title": [{"type": "text", "text": {"content": title[:100]}}]},
        "Tags": {"multi_select": [{"name": t} for t in tags]},
        "Content Hash": {"rich_text": [{"type": "text", "text": {"content": content_hash}}]},
    }
    created = fm.get("created", "")
    if created:
        properties["Created"] = {"date": {"start": str(created)}}
    if updated:
        properties["Updated"] = {"date": {"start": str(updated)}}
    if sources_str:
        properties["Sources"] = {"rich_text": [{"type": "text", "text": {"content": sources_str[:2000]}}]}
    if page_type and db_id == TYPE_TO_DB.get("entity"):
        properties["Type"] = {"select": {"name": page_type}}

    notion_request("PATCH", f"/pages/{notion_page_id}", {"properties": properties})
    time.sleep(0.4)

    print(f"  ✓ Updated: {title} (hash: {content_hash[:16]}...)")
    return True

def archive_orphaned_pages(existing_pages, local_files, dry_run=False):
    """Archive Notion pages whose Wiki File points to a deleted local file.

    Args:
        existing_pages: dict from get_existing_pages(), keyed by Wiki File path.
                        This should be the *combined* dict across all databases
                        that were synced in this run.
        local_files: set of relative Wiki File paths that still exist locally.
        dry_run: if True, only print what would be archived.

    Returns:
        Number of pages archived (or would-be archived).
    """
    archived = 0
    for wiki_file, page_info in existing_pages.items():
        if not wiki_file:
            continue  # skip pages without a Wiki File property
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
    """Collect all wiki pages, optionally filtered by type."""
    pages = []
    dirs_to_scan = []
    if wiki_type:
        for d, t in DIR_TO_TYPE.items():
            if t == wiki_type:
                dirs_to_scan.append(d)
    else:
        dirs_to_scan = list(DIR_TO_TYPE.keys())

    for d in dirs_to_scan:
        dir_path = WIKI_DIR / d
        if dir_path.exists():
            for f in sorted(dir_path.glob("*.md")):
                pages.append((f, DIR_TO_TYPE[d]))
    return pages

def main():
    parser = argparse.ArgumentParser(description="Sync LLM Wiki: Local <-> NAS <-> Notion")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Sync all pages to Notion")
    group.add_argument("--type", choices=["entity", "concept", "comparison", "query"], help="Sync pages of type")
    group.add_argument("--file", help="Sync a single file")
    group.add_argument("--list", action="store_true", help="List sync status")
    group.add_argument("--sync", action="store_true", help="Full sync: local -> Notion")
    parser.add_argument("--dry-run", action="store_true", help="Preview without syncing")
    parser.add_argument("--force", action="store_true",
                        help="Force re-sync all pages (ignore content hash, update properties). "
                             "Use to fix missing tags/dates in Notion.")
    parser.add_argument("--delete", action="store_true",
                        help="Archive Notion pages whose local wiki file has been deleted. "
                             "Use with --all, --type, or --sync.")
    args = parser.parse_args()

    # --sync: Local -> Notion (full pipeline, no NAS)
    if args.sync:
        if not args.dry_run:
            print("--- Syncing to Notion ---\n")
        args.all = True

    if not NOTION_KEY and not args.list:
        print("ERROR: NOTION_API_KEY not set. Add it to ~/.hermes/.env")
        sys.exit(1)

    if args.list:
        print("Wiki -> Notion sync status:\n")
        all_pages = collect_wiki_pages()
        existing_cache = {}
        for f, ptype in all_pages:
            db_id = TYPE_TO_DB[ptype]
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

    if args.file:
        fpath = Path(args.file).expanduser().resolve()
        if not fpath.exists():
            print(f"ERROR: File not found: {fpath}")
            sys.exit(1)
        ptype = None
        for d, t in DIR_TO_TYPE.items():
            if d in str(fpath):
                ptype = t
                break
        if not ptype:
            print(f"ERROR: Cannot determine page type from path: {fpath}")
            sys.exit(1)
        db_id = TYPE_TO_DB[ptype]
        print(f"Syncing single file: {fpath.name} ({ptype})")

        # Check existing pages to avoid duplicates
        existing = get_existing_pages(db_id)
        rel_path = str(fpath.relative_to(WIKI_DIR))
        local_hash = file_hash(fpath)

        if rel_path in existing:
            ep = existing[rel_path]
            notion_hash = ep.get("content_hash", "")
            if local_hash == notion_hash:
                print(f"  Skipped (up-to-date): {fpath.name}")
            else:
                print(f"  Updating (hash changed): {fpath.name}")
                update_page(fpath, ep["page_id"], db_id, dry_run=args.dry_run)
        else:
            sync_page(fpath, db_id, dry_run=args.dry_run)
        return

    # --all or --type: sync all pages of given type(s)
    pages = collect_wiki_pages(wiki_type=args.type)

    # Build existing_cache for sync AND delete (even if no local pages exist)
    existing_cache = {}
    if pages:
        target_types = set(ptype for _, ptype in pages)
    elif args.type:
        target_types = {args.type}
    else:
        target_types = set(TYPE_TO_DB.keys())
    for t in target_types:
        db_id = TYPE_TO_DB[t]
        existing_cache[db_id] = get_existing_pages(db_id)

    if not pages and not args.delete:
        print("No wiki pages found.")
        return

    print(f"Found {len(pages)} wiki page(s) to sync.\n")

    created = 0
    updated = 0
    skipped = 0

    for fpath, ptype in pages:
        db_id = TYPE_TO_DB[ptype]
        if db_id not in existing_cache:
            existing_cache[db_id] = get_existing_pages(db_id)

        rel_path = str(fpath.relative_to(WIKI_DIR))
        existing = existing_cache[db_id]

        # Compute local content hash
        local_hash = file_hash(fpath)

        if rel_path in existing:
            ep = existing[rel_path]
            notion_hash = ep.get("content_hash", "")

            if local_hash != notion_hash or args.force:
                action = "FORCE" if args.force and local_hash == notion_hash else "UPDATE"
                print(f"[{action}] {fpath.name} (hash changed)" if action == "UPDATE" else f"[{action}] {fpath.name} (re-syncing properties)")
                update_page(fpath, ep["page_id"], db_id, dry_run=args.dry_run)
                updated += 1
            else:
                skipped += 1
        else:
            print(f"[CREATE] {fpath.name}")
            sync_page(fpath, db_id, dry_run=args.dry_run)
            created += 1

        time.sleep(0.4)

    print(f"\nDone. Created: {created}, Updated: {updated}, Skipped (up-to-date): {skipped}")

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
