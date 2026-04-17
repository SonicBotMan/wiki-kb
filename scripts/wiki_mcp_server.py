#!/usr/bin/env python3
"""
Wiki Brain MCP Server — 暴露 Wiki + Entity Registry 为 MCP tools。
使用 FastMCP + stdio 传输，供 Hermes Agent 调用。
"""

import json
import os
import sys
import re
import logging
import signal
import tempfile
import fcntl
from pathlib import Path
from datetime import datetime
import hashlib
import subprocess

# ============================================================
# 0. 日志配置
# ============================================================
LOG_DIR = Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki"))) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("wiki-brain")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _fh = logging.FileHandler(LOG_DIR / "wiki-brain.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_fh)
    _sh = logging.StreamHandler(sys.stderr)
    _sh.setLevel(logging.WARNING)
    _sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _logger.addHandler(_sh)
from typing import Optional, List, Dict

# ============================================================
# 1. 检查 mcp 包是否已安装
# ============================================================
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("=" * 60, file=sys.stderr)
    print("ERROR: mcp package not installed", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Install with: pip install mcp", file=sys.stderr)
    print("  ~/.hermes/hermes-agent/venv/bin/pip install mcp", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)

# ============================================================
# 2. 路径配置
# ============================================================
WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki")))
REGISTRY_FILE = WIKI_ROOT / "registry.json"

# OpenViking API 配置
# Support both OPENVIKING_ENDPOINT (full URL) and HOST/PORT (separate)
_OV_ENDPOINT = os.environ.get("OPENVIKING_ENDPOINT", "")
if _OV_ENDPOINT:
    OPENVIKING_URL = _OV_ENDPOINT.rstrip("/")
else:
    OPENVIKING_HOST = os.environ.get("OPENVIKING_HOST", "localhost")
    OPENVIKING_PORT = int(os.environ.get("OPENVIKING_PORT", "1933"))
    OPENVIKING_URL = f"http://{OPENVIKING_HOST}:{OPENVIKING_PORT}"
OPENVIKING_API_KEY = os.environ.get("OPENVIKING_API_KEY", "")
OPENVIKING_ACCOUNT = os.environ.get("OPENVIKING_ACCOUNT", "hermes")
OPENVIKING_USER = os.environ.get("OPENVIKING_USER", "default")

# ============================================================
# 3. Import entity_registry module
# ============================================================
sys.path.insert(0, str(WIKI_ROOT / "scripts"))
from wiki_utils import get_frontmatter as _get_frontmatter, TYPE_DIR_MAP, ALLOWED_SUBDIRS, ALLOWED_TYPES

_REGISTRY_AVAILABLE = False
_REGISTRY_IMPORT_ERROR = ""
try:
    import entity_registry
    _REGISTRY_AVAILABLE = True
except ImportError as e:
    _REGISTRY_IMPORT_ERROR = str(e)
    _logger.error(
        "entity_registry module import failed: %s — "
        "Entity-related tools will be unavailable. "
        "Check that /app/scripts/entity_registry.py exists.",
        e
    )

# ============================================================
# 4. 路径安全工具
# ============================================================

# 允许的 wiki 子目录白名单
# 允许的 entity/page 类型
# 扫描时排除的目录（系统产物、原始文件，非 wiki 内容）
_EXCLUDE_DIRS = {'dream-reports', 'raw', 'src', 'logs', 'scripts', '.git',
                 'assets', 'papers', 'transcripts', 'articles',
                 'images', 'pdfs', 'videos', 'audio'}

# Rejection feedback storage
_FEEDBACK_FILE = WIKI_ROOT / ".wiki_feedback.json"
_GIT_COMMIT_PREFIX = "[wiki-brain]"


def _load_feedback() -> dict:
    """Load rejection feedback from JSON file."""
    if not _FEEDBACK_FILE.exists():
        return {}
    try:
        return json.loads(_FEEDBACK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _logger.warning("Failed to load feedback: %s", e)
        return {}


def _save_feedback(fb: dict):
    """Save rejection feedback to JSON file."""
    try:
        _safe_write(_FEEDBACK_FILE, json.dumps(fb, ensure_ascii=False, indent=2))
    except OSError as e:
        _logger.error("Failed to save feedback: %s", e)


def _store_rejection(page_id: str, feedback_text: str):
    """Store a review rejection for future reference."""
    fb = _load_feedback()
    if page_id not in fb:
        fb[page_id] = []
    fb[page_id].append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "feedback": feedback_text,
    })
    fb[page_id] = fb[page_id][-5:]  # keep last 5
    _save_feedback(fb)
    _logger.info("Stored rejection feedback for %s", page_id)


def _get_rejection_history(page_id: str) -> list:
    """Get rejection history for a page (most recent first)."""
    fb = _load_feedback()
    return list(reversed(fb.get(page_id, [])))


def _clear_rejection_history(page_id: str):
    """Clear rejection history after successful review."""
    fb = _load_feedback()
    if page_id in fb:
        del fb[page_id]
        _save_feedback(fb)


def _git_init():
    """Initialize git repo in WIKI_ROOT if not already."""
    if (WIKI_ROOT / ".git").exists():
        return
    _logger.info("Initializing git repo in %s", WIKI_ROOT)
    subprocess.run(["git", "init"], cwd=str(WIKI_ROOT), capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "wiki-brain@hermes.local"], cwd=str(WIKI_ROOT), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Wiki Brain"], cwd=str(WIKI_ROOT), capture_output=True)
    (WIKI_ROOT / ".gitignore").write_text(
        "logs/\n*.tmp\n*.lock\n__pycache__/\n.wiki_feedback.json\nregistry.json\n",
        encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=str(WIKI_ROOT), capture_output=True)
    subprocess.run(["git", "commit", "-m", "[wiki-brain] init: initial commit"],
                   cwd=str(WIKI_ROOT), capture_output=True, text=True)


def _git_commit(message: str):
    """Auto-commit changes with [wiki-brain] prefix. No-op if no changes."""
    if not (WIKI_ROOT / ".git").exists():
        return
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(WIKI_ROOT),
                       capture_output=True, text=True)
        result = subprocess.run(
            ["git", "commit", "-m", f"{_GIT_COMMIT_PREFIX} {message}"],
            cwd=str(WIKI_ROOT), capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            _logger.info("Git commit: %s", message)
    except (subprocess.TimeoutExpired, OSError) as e:
        _logger.warning("Git commit failed: %s", e)



def _is_excluded(md_file) -> bool:
    """检查文件是否在排除目录中"""
    try:
        rel = md_file.relative_to(WIKI_ROOT)
        return rel.parts[0] in _EXCLUDE_DIRS if rel.parts else False
    except ValueError:
        return True

def _validate_path(path: Path) -> Path:
    """验证路径不会逃逸 WIKI_ROOT（防 path traversal）。"""
    try:
        resolved = path.resolve()
        wiki_resolved = WIKI_ROOT.resolve()
        if not str(resolved).startswith(str(wiki_resolved) + os.sep) and resolved != wiki_resolved:
            raise ValueError(f"Path traversal blocked: {path} is outside WIKI_ROOT")
        return resolved
    except (OSError, ValueError) as e:
        raise ValueError(f"Invalid path: {e}")


def _validate_type(entity_type: str) -> str:
    """验证 entity/page 类型是否合法。"""
    normalized = entity_type.lower().strip()
    if normalized not in ALLOWED_TYPES:
        raise ValueError(f"Invalid type: '{entity_type}'. Allowed: {ALLOWED_TYPES}")
    return normalized


def _safe_write(path: Path, content: str):
    """Atomic write: tmpfile + fsync + rename. Prevents corruption on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise



def _compute_body_hash(body: str) -> str:
    """SHA256 of body content (first 16 hex chars). Detects manual edits."""
    return hashlib.sha256(body.strip().encode('utf-8')).hexdigest()[:16]


class _FileLock:
    """File-based lock using fcntl.flock for concurrent write protection."""

    def __init__(self, path: Path):
        self.lock_path = path.with_suffix(".lock")
        self._fd = None

    def __enter__(self):
        self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None



# ============================================================
# 5. Wiki 工具函数
# ============================================================

def _slugify(name: str) -> str:
    """将名称转换为合法的文件名 slug"""
    name = name.lower().strip()
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[\s_]+', '-', name)
    name = re.sub(r'-+', '-', name)
    return name.strip('-')


def _resolve_page_path(page_id: str) -> Optional[Path]:
    """解析 page_id 为完整路径（带路径安全验证）"""
    page_id = page_id.strip()

    # 如果已经是完整路径
    if '/' in page_id:
        path = WIKI_ROOT / page_id
        if path.exists():
            return _validate_path(path)
        # 尝试加 .md
        path = WIKI_ROOT / f"{page_id}.md"
        if path.exists():
            return _validate_path(path)

    # 搜索所有子目录
    for subdir in sorted(ALLOWED_SUBDIRS):
        path = WIKI_ROOT / subdir / f"{page_id}.md"
        if path.exists():
            return _validate_path(path)
        # 模糊匹配
        if '-' in page_id or '_' in page_id:
            for f in (WIKI_ROOT / subdir).glob("*"):
                if f.stem.replace('-', '').replace('_', '') == page_id.replace('-', '').replace('_', ''):
                    return _validate_path(f)

    # 搜索所有 .md 文件
    for f in WIKI_ROOT.rglob("*.md"):
        if _is_excluded(f):
            continue
        if f.stem == page_id or f.stem.replace('-', '') == page_id.replace('-', ''):
            return _validate_path(f)

    return None




def _update_frontmatter(content: str, updates: dict) -> str:
    """更新 frontmatter 中的指定字段"""
    fm, body = _get_frontmatter(content)
    fm.update(updates)

    fm_lines = []
    for key, val in fm.items():
        fm_lines.append(f"{key}: {val}")

    return f"---\n" + "\n".join(fm_lines) + "\n---\n" + body


def _get_section_content(body: str, section: str) -> str:
    """从 body 中提取指定 section 的内容"""
    lines = body.split('\n')
    in_section = False
    section_lines = []
    section_header = f"## {section}"

    for line in lines:
        if line.strip() == section_header:
            in_section = True
            continue
        if in_section and line.startswith('## '):
            break
        if in_section:
            section_lines.append(line)

    return '\n'.join(section_lines).strip()


def _update_section(body: str, section: str, content: str) -> str:
    """更新 body 中指定 section 的内容"""
    lines = body.split('\n')
    new_lines = []
    in_section = False
    section_found = False
    section_header = f"## {section}"

    for line in lines:
        if line.strip() == section_header:
            in_section = True
            section_found = True
            new_lines.append(line)
            new_lines.append(content)
            continue
        if in_section and line.startswith('## '):
            in_section = False
            new_lines.append(line)
            continue
        if not in_section:
            new_lines.append(line)

    # 如果 section 不存在，在适当位置插入
    if not section_found:
        # 在 Timeline 之前插入
        timeline_idx = None
        for i, line in enumerate(new_lines):
            if line.strip() == '---':
                timeline_idx = i
                break

        if timeline_idx is not None:
            new_lines.insert(timeline_idx, f"## {section}\n{content}")
        else:
            new_lines.append(f"## {section}\n{content}")

    return '\n'.join(new_lines)


def _openviking_search(query: str, type_filter: str = "") -> List[Dict]:
    """调用 OpenViking API 进行语义搜索"""
    import urllib.request
    import urllib.parse

    try:
        url = f"{OPENVIKING_URL}/api/v1/search/search"
        payload = {"query": query, "limit": 10}
        if type_filter:
            payload["type"] = type_filter
        data = json.dumps(payload).encode('utf-8')
        headers = {"Content-Type": "application/json"}
        if OPENVIKING_API_KEY:
            headers["X-API-Key"] = OPENVIKING_API_KEY
        if OPENVIKING_ACCOUNT:
            headers["X-OpenViking-Account"] = OPENVIKING_ACCOUNT
        if OPENVIKING_USER:
            headers["X-OpenViking-User"] = OPENVIKING_USER
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            _logger.warning("OpenViking HTTP %s for query: %s", e.code, query)
            return []
        except urllib.error.URLError as e:
            _logger.warning("OpenViking connection failed: %s (query: %s)", e.reason, query)
            return []
        except Exception as e:
            _logger.warning("OpenViking request failed: %s (query: %s)", e, query)
            return []
        # Parse OpenViking v1 response format
        if result.get("status") == "ok":
            items = []
            for r in (result.get("result", {}).get("resources") or []):
                uri = r.get("uri", "")
                # 从 URI 提取 slug 和 title
                # URI format: viking://resources/<slug>/<subpath>.md
                # 或: viking://resources/<prefix>/.../<slug>.md/<nested>/...
                # parts[0]=viking: parts[1]= (empty) parts[2]=resources ...
                # slug 是第一个 .md 组件（去掉 .md 后缀）
                parts = uri.rstrip("/").split("/")
                slug = ""
                for p in parts[3:]:  # 跳过 viking:, 空, resources
                    if p.endswith(".md"):
                        slug = p[:-3]  # 去掉 .md 后缀
                        break
                if not slug:
                    slug = parts[-1] if len(parts) >= 2 else parts[-1]
                title = slug.replace("_", " ")
                # Resolve page_path by matching local files for this slug
                resolved_path = ""
                resolved_type = ""
                file_slug = _slugify(slug)
                for _sdir in sorted(ALLOWED_SUBDIRS):
                    candidate = WIKI_ROOT / _sdir / f"{file_slug}.md"
                    if candidate.exists():
                        try:
                            _c = candidate.read_text(encoding="utf-8")
                            _fm, _ = _get_frontmatter(_c)
                            resolved_path = str(candidate.relative_to(WIKI_ROOT))
                            resolved_type = _fm.get("type", "")
                        except Exception:
                            pass
                        break
                items.append({
                    "title": title,
                    "type": resolved_type,
                    "page_path": resolved_path,
                    "summary": r.get("abstract", ""),
                })

            _logger.info("openviking_search: query=%r type=%r → %d results", query, type_filter, len(items))
            # Deduplicate: prefer page_path, fallback to title
            seen = set()
            unique = []
            for item in items:
                key = item.get("page_path") or item.get("title", "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                unique.append(item)
            return unique
        else:
            err = result.get("error", {})
            _logger.warning("openviking_search: query=%r → error: %s", query, err.get("message", result))
        return []

    except Exception as e:
        _logger.warning("openviking_search: query=%r → fallback (%s: %s)", query, type(e).__name__, e)
        # 如果 OpenViking 不可用，fallback 到文件搜索
        return _fallback_file_search(query, type_filter)

def _fallback_file_search(query: str, type_filter: str = "") -> List[Dict]:
    """当 OpenViking 不可用时，使用文件搜索 fallback"""
    results = []
    query_lower = query.lower()

    for md_file in WIKI_ROOT.rglob("*.md"):
        # 跳过非内容文件
        if md_file.name in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']:
            continue
        if _is_excluded(md_file):
            continue

        try:
            content = md_file.read_text(encoding='utf-8')
            fm, body = _get_frontmatter(content)

            # 类型过滤
            if type_filter and fm.get('type') != type_filter:
                continue

            # 简单文本匹配
            if query_lower in content.lower():
                rel_path = md_file.relative_to(WIKI_ROOT)
                results.append({
                    "title": fm.get('title', md_file.stem),
                    "type": fm.get('type', 'unknown'),
                    "page_path": str(rel_path),
                    "summary": body[:200].replace('\n', ' ').strip() if body else ""
                })
        except Exception as e:
            _logger.warning("Failed to read page %s: %s: %s", md_file.name, type(e).__name__, e)

    return results


# ============================================================
# 5. 创建 FastMCP Server
# ============================================================
mcp = FastMCP("wiki-brain", host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "8764")))


# ============================================================
# 6. Wiki 操作 Tools
# ============================================================

@mcp.tool()
def wiki_search(query: str, type: str = "") -> List[Dict]:
    """
    语义搜索 wiki 页面。
    调用 OpenViking search API，支持按 type 过滤。
    返回 [{title, type, page_path, summary}]
    """
    results = _openviking_search(query, type)
    return results


@mcp.tool()
def wiki_get(page_id: str) -> Dict:
    """
    读取 wiki 页面完整内容。
    page_id 可以是文件名（如 "hermes-agent"）或完整路径。
    返回 {title, type, frontmatter, executive_summary, key_facts, relations, timeline}
    """
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"Page not found: {page_id}")

    content = path.read_text(encoding='utf-8')
    fm, body = _get_frontmatter(content)

    # 提取各 section
    summary = _get_section_content(body, "Executive Summary")
    key_facts = _get_section_content(body, "Key Facts")
    relations = _get_section_content(body, "Relations")
    timeline = _get_section_content(body, "Timeline")

    return {
        "title": fm.get('title', path.stem),
        "type": fm.get('type', 'unknown'),
        "page_path": str(path.relative_to(WIKI_ROOT)),
        "frontmatter": fm,
        "executive_summary": summary,
        "key_facts": key_facts,
        "relations": relations,
            "content_hash": fm.get("content_hash", ""),
            "rejection_history": _get_rejection_history(page_id),
        "timeline": timeline,
        "raw_body": body
    }


@mcp.tool()
def wiki_create(name: str, type: str, description: str, content: str = "", status: str = "draft") -> Dict:
    """
    Create a new wiki page with draft status.
    Pages are created as "draft" -- call wiki_review() to promote to "active".
    Auto-registers to Entity Registry. Returns {page_path, entity_id, status}.
    """
    # 验证 type
    type = _validate_type(type)
    
    # Input sanitization: prevent YAML injection via newlines
    name = name.replace("\n", " ").replace("\r", " ").strip()
    status = status.replace("\n", " ").replace("\r", " ").strip()
    if len(name) > 200:
        raise ValueError("Page name too long (max 200 characters)")
    
    # 内容长度限制 (1MB)
    if len(description) > 500_000 or len(content) > 1_000_000:
        raise ValueError("Content too large: description max 500KB, content max 1MB")
    
    # 确定目录


    subdir = TYPE_DIR_MAP.get(type.lower(), "concepts")
    slug = _slugify(name)
    # SEC-2: Empty slug guard
    if not slug:
        raise ValueError("Cannot create page: name produced an empty slug")
    page_path = WIKI_ROOT / subdir / f"{slug}.md"

    if page_path.exists():
        raise ValueError(f"Page already exists: {page_path}")

    # 生成 frontmatter
    now = datetime.now().strftime("%Y-%m-%d")
    default_content = content if content else description

    # Build frontmatter safely with yaml.dump (prevents YAML injection)
    import yaml
    fm_dict = {
        "title": name,
        "created": now,
        "updated": now,
        "type": type,
        "tags": [],
        "sources": [],
        "status": status,
        "content_hash": "",
    }
    fm_yaml = yaml.dump(fm_dict, default_flow_style=None, allow_unicode=True, sort_keys=False).rstrip("\n")

    page_content = f"""---
{fm_yaml}
---

# {name}

## Executive Summary

{default_content}

## Key Facts

-

## Relations
| Relation | Target | Note |
|----------|--------|------|
| related | [[]] | |

---

## Timeline

- **{now}** | Page created
  [Source: wiki_mcp_server]
"""

    page_path.parent.mkdir(parents=True, exist_ok=True)

    with _FileLock(page_path):
        if page_path.exists():
            raise ValueError(f"Page already exists: {page_path}")
        # Compute content hash from the body portion
        _, new_body = _get_frontmatter(page_content)
        import yaml as _yaml_h
        page_fm = _get_frontmatter(page_content)[0]
        page_fm['content_hash'] = _compute_body_hash(new_body)
        fm_yaml = _yaml_h.dump(page_fm, default_flow_style=None, allow_unicode=True, sort_keys=False).rstrip("\n")
        body_part = page_content.split("\n---\n", 1)[1] if "\n---\n" in page_content else page_content
        page_content = f'---\n{fm_yaml}\n---\n{body_part}'
        _safe_write(page_path, page_content)
        _git_commit(f"create: {page_path.name}")

    # Entity registration: register if available, rollback on failure
    rel_path = str(page_path.relative_to(WIKI_ROOT))
    entity_id = None

    if _REGISTRY_AVAILABLE:
        try:
            entity = entity_registry.register(
                name=name,
                entity_type=type,
                page_path=rel_path
            )
            entity_id = entity.get("id")
        except Exception as e:
            _logger.warning("Entity registration failed, rolling back page %s: %s", rel_path, e)
            # Rollback: remove created page since entity is broken
            try:
                page_path.unlink()
                _logger.info("Rolled back page: %s", rel_path)
            except OSError as del_err:
                _logger.error("Rollback failed for %s: %s", rel_path, del_err)
            raise RuntimeError(
                f"wiki_create failed: entity registration error ({e}). "
                f"Page rolled back. Check entity_registry."
            ) from e
    else:
        _logger.warning("entity_registry unavailable, skipping registration (page: %s)", rel_path)

    return {
        "page_path": rel_path,
        "entity_id": entity_id,
        "status": status,
        "title": name,
        "type": type
    }
@mcp.tool()
def wiki_update(page_id: str, section: str, content: str) -> Dict:
    """
    Update a section (Executive Summary / Key Facts / Relations).
    Timeline is append-only — use wiki_append_timeline() instead.
    Returns {page_path, updated_sections}
    """
    MAX_SECTION_CONTENT = 500_000
    if len(content) > MAX_SECTION_CONTENT:
        raise ValueError(f"Section content exceeds maximum allowed size ({MAX_SECTION_CONTENT} chars)")
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"Page not found: {page_id}")

    section_normalized = section.strip().lower()

    # Timeline: always reject, guide to dedicated API
    if section_normalized == "timeline":
        raise ValueError(
            "Cannot update Timeline via wiki_update (Timeline is append-only). "
            "Use wiki_append_timeline(page_id, event, source) instead."
        )

    # Section whitelist
    UPDATABLE_SECTIONS = {
        "executive summary": "Executive Summary",
        "key facts": "Key Facts",
        "relations": "Relations",
    }
    canonical_section = UPDATABLE_SECTIONS.get(section_normalized)
    if canonical_section is None:
        raise ValueError(
            f"Cannot update section '{section}'. "
            f"Allowed: {list(UPDATABLE_SECTIONS.values())}. "
            f"For Timeline, use wiki_append_timeline."
        )

    with _FileLock(path):
        original = path.read_text(encoding='utf-8')
        fm, body = _get_frontmatter(original)

        # Content hash check: detect manual edits since last agent write
        stored_hash = fm.get("content_hash", "")
        current_hash = _compute_body_hash(body)
        hash_warning = None
        if stored_hash and stored_hash != current_hash:
            hash_warning = f"Content changed since last write (hash {stored_hash}->{current_hash}). Possible manual edit."
            _logger.warning("%s: %s", str(path.relative_to(WIKI_ROOT)), hash_warning)


        body = _update_section(body, canonical_section, content)

        fm['content_hash'] = _compute_body_hash(body)
        fm['updated'] = datetime.now().strftime("%Y-%m-%d")
        fm_lines = [f"{k}: {v}" for k, v in fm.items()]
        new_content = f"---\n" + "\n".join(fm_lines) + "\n---\n" + body

        _safe_write(path, new_content)
        _git_commit(f"update {canonical_section}: {path.name}")

    rel_path = str(path.relative_to(WIKI_ROOT))
    return {
        "page_path": rel_path,
        "updated_sections": [canonical_section],
        "section_content": content,
        "hash_warning": hash_warning,
    }

@mcp.tool()
def wiki_append_timeline(page_id: str, event: str, source: str = "") -> Dict:
    """
    向指定页面追加 Timeline 条目。
    自动格式化为 `- **YYYY-MM-DD** | event \n  [Source: source]`
    更新 frontmatter updated 日期。
    返回 {page_path, timeline_entry}
    """
    MAX_TIMELINE_EVENT = 10_000
    if len(event) > MAX_TIMELINE_EVENT:
        raise ValueError(f"Timeline event exceeds maximum allowed size ({MAX_TIMELINE_EVENT} chars)")
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"Page not found: {page_id}")


    with _FileLock(path):
        original = path.read_text(encoding='utf-8')
        fm, body = _get_frontmatter(original)

        # Content hash check
        stored_hash = fm.get("content_hash", "")
        current_hash = _compute_body_hash(body)
        if stored_hash and stored_hash != current_hash:
            _logger.warning("%s: content changed since last write (hash %s->%s)",
                str(path.relative_to(WIKI_ROOT)), stored_hash, current_hash)


        # 格式化 timeline 条目
        now = datetime.now().strftime("%Y-%m-%d")
        source_str = f"\n  [Source: {source}]" if source else ""
        entry = f"- **{now}** | {event}{source_str}\n"

        # 追加到 Timeline
        timeline_marker = "## Timeline"
        if timeline_marker in body:
            # 在 Timeline section 追加
            parts = body.split(timeline_marker)
            before = parts[0]
            after = parts[1] if len(parts) > 1 else ""

            # 找到 --- 分隔线
            if "---" in after:
                sep_idx = after.index("---")
                after = after[:sep_idx] + entry + "---\n" + after[sep_idx+3:]
            else:
                after = entry + after

            body = before + timeline_marker + after
        else:
            # Timeline 不存在，创建它
            body = body.rstrip()
            if not body.endswith('\n'):
                body += '\n'
            body += f"\n---\n\n## Timeline\n\n{entry}"

        # 更新 frontmatter
        fm['updated'] = now

        # Reassemble
        fm_lines = [f"{k}: {v}" for k, v in fm.items()]
        new_content = f"---\n" + "\n".join(fm_lines) + "\n---\n" + body

        _safe_write(path, new_content)
        _git_commit(f"timeline: {path.name}")


    rel_path = str(path.relative_to(WIKI_ROOT))
    return {
        "page_path": rel_path,
        "timeline_entry": entry.strip()
    }


@mcp.tool()
def wiki_list(type: str = "", status: str = "") -> List[Dict]:
    """
    列出 wiki 页面。
    可选按 type 和 status 过滤。
    返回 [{id, title, type, status, updated}]
    """
    pages = []

    for md_file in WIKI_ROOT.rglob("*.md"):
        # 跳过非内容文件
        if md_file.name in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']:
            continue
        if _is_excluded(md_file):
            continue

        try:
            content = md_file.read_text(encoding='utf-8')
            fm, _ = _get_frontmatter(content)

            # 类型过滤
            if type and fm.get('type') != type:
                continue

            # 状态过滤
            if status and fm.get('status') != status:
                continue

            pages.append({
                "id": md_file.stem,
                "title": fm.get('title', md_file.stem),
                "type": fm.get('type', 'unknown'),
                "status": fm.get('status', 'unknown'),
                "updated": fm.get('updated', ''),
                "page_path": str(md_file.relative_to(WIKI_ROOT))
            })
        except Exception as e:
            _logger.warning("Failed to read page %s: %s: %s", md_file.name, type(e).__name__, e)

    return pages


# ============================================================
# 7. Entity Registry Tools
# ============================================================

@mcp.tool()
def entity_resolve(name: str) -> Optional[Dict]:
    """
    通过名称或别名解析实体。
    返回 {id, canonical_name, aliases, type, primary_page}
    """
    if not _REGISTRY_AVAILABLE:
        raise RuntimeError(f"entity_registry module unavailable: {_REGISTRY_IMPORT_ERROR}")
    entity = entity_registry.resolve(name)
    if entity:
        return {
            "id": entity.get("id"),
            "canonical_name": entity.get("canonical_name"),
            "aliases": entity.get("aliases", []),
            "type": entity.get("type"),
            "primary_page": entity.get("primary_page")
        }
    return None


@mcp.tool()
def entity_register(name: str, type: str, aliases: List[str] = None) -> Dict:
    """
    注册新实体。
    返回 {id, canonical_name, aliases}
    """
    if not _REGISTRY_AVAILABLE:
        raise RuntimeError(f"entity_registry module unavailable: {_REGISTRY_IMPORT_ERROR}")
    type = _validate_type(type)
    entity = entity_registry.register(
        name=name,
        entity_type=type,
        aliases=aliases or []
    )
    return {
        "id": entity.get("id"),
        "canonical_name": entity.get("canonical_name"),
        "aliases": entity.get("aliases", [])
    }


@mcp.tool()
def entity_list(type: str = "") -> List[Dict]:
    """
    列出实体。
    可选按 type 过滤。
    返回实体列表。
    """
    if not _REGISTRY_AVAILABLE:
        raise RuntimeError(f"entity_registry module unavailable: {_REGISTRY_IMPORT_ERROR}")
    entities = entity_registry.get_all_entities()
    if type:
        entities = [e for e in entities if e.get("type") == type]

    return [
        {
            "id": e.get("id"),
            "canonical_name": e.get("canonical_name"),
            "aliases": e.get("aliases", []),
            "type": e.get("type"),
            "primary_page": e.get("primary_page"),
            "updated": e.get("updated")
        }
        for e in entities
    ]


@mcp.tool()
def entity_merge(id1: str, id2: str) -> Dict:
    """
    合并两个实体。
    id1 被合并到 id2，id1 删除。
    返回合并结果。
    """
    if not _REGISTRY_AVAILABLE:
        raise RuntimeError(f"entity_registry module unavailable: {_REGISTRY_IMPORT_ERROR}")
    success = entity_registry.merge(id1, id2)
    if success:
        return {
            "success": True,
            "merged_id": id1,
            "into_id": id2,
            "message": f"Entity {id1} merged into {id2}"
        }
    else:
        return {
            "success": False,
            "error": "Merge failed: one or both entities do not exist"
        }


# ============================================================
# 8. 系统 Tool
# ============================================================

@mcp.tool()
def wiki_stats() -> Dict:
    """
    返回 wiki 统计：总页面数、各类型数量、registry 统计等。
    """
    # 统计页面
    page_counts = {}
    total_pages = 0

    for md_file in WIKI_ROOT.rglob("*.md"):
        if md_file.name in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']:
            continue
        if _is_excluded(md_file):
            continue
        total_pages += 1

        try:
            content = md_file.read_text(encoding='utf-8')
            fm, _ = _get_frontmatter(content)
            ptype = fm.get('type', 'unknown')
            page_counts[ptype] = page_counts.get(ptype, 0) + 1
        except Exception as e:
            _logger.warning("Failed to read page %s: %s: %s", md_file.name, type(e).__name__, e)

    # Registry 统计
    if _REGISTRY_AVAILABLE:
        try:
            reg = entity_registry.load_registry()
            reg_stats = reg.get("stats", {})
            reg_version = reg.get("version", 1)
        except Exception as e:
            _logger.warning("Failed to load registry for stats: %s", e)
            reg_stats = {}
            reg_version = 0
    else:
        reg_stats = {}
        reg_version = 0

    return {
        "total_pages": total_pages,
        "pages_by_type": page_counts,
        "total_entities": reg_stats.get("total_entities", 0),
        "total_aliases": reg_stats.get("total_aliases", 0),
        "registry_version": reg_version
    }


# ============================================================
# 8.5 Health Check
# ============================================================

@mcp.tool()
def wiki_health() -> Dict:
    """
    Health check: verify wiki core dependencies status.
    Returns {status, checks: [{name, status, message}]}
    """
    import time
    checks = []

    # 1. Registry file integrity
    try:
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                json.load(f)
            checks.append({"name": "registry", "status": "ok", "message": "registry.json intact"})
        else:
            checks.append({"name": "registry", "status": "ok", "message": "registry.json missing (will be created on first write)"})
    except (json.JSONDecodeError, IOError) as e:
        checks.append({"name": "registry", "status": "error", "message": f"registry.json corrupted: {e}"})

    # 2. Entity registry availability
    if _REGISTRY_AVAILABLE:
        checks.append({"name": "entity_registry", "status": "ok", "message": "entity_registry module loaded"})
    else:
        checks.append({"name": "entity_registry", "status": "warning", "message": f"entity_registry unavailable: {_REGISTRY_IMPORT_ERROR}"})

    # 3. Wiki directory writable
    try:
        test_file = WIKI_ROOT / ".health_check_tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        checks.append({"name": "disk_writable", "status": "ok", "message": f"writable: {WIKI_ROOT}"})
    except Exception as e:
        checks.append({"name": "disk_writable", "status": "error", "message": str(e)})

    # 4. Disk space
    try:
        stat = os.statvfs(str(WIKI_ROOT))
        if stat.f_blocks > 0:
            free_pct = (stat.f_bavail / stat.f_blocks) * 100
        else:
            free_pct = 0
        if free_pct < 10:
            checks.append({"name": "disk_space", "status": "warning",
                           "message": f"Low disk space: {free_pct:.1f}% available"})
        else:
            checks.append({"name": "disk_space", "status": "ok",
                           "message": f"Disk space OK: {free_pct:.1f}% available"})
    except Exception as e:
        checks.append({"name": "disk_space", "status": "error", "message": str(e)})

    # 5. OpenViking connectivity
    try:
        import urllib.request
        start = time.time()
        req = urllib.request.Request(
            f"{OPENVIKING_URL}/health",
            method="GET",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                ms = (time.time() - start) * 1000
                checks.append({"name": "openviking", "status": "ok",
                               "message": f"OpenViking reachable ({ms:.0f}ms)"})
            else:
                checks.append({"name": "openviking", "status": "warning",
                               "message": f"OpenViking returned {resp.status}"})
    except Exception as e:
        checks.append({"name": "openviking", "status": "warning",
                       "message": f"OpenViking unreachable: {type(e).__name__}"})

    # 6. Page count
    try:
        page_count = sum(1 for d in ALLOWED_SUBDIRS
                        for f in (WIKI_ROOT / d).glob("*.md") if not f.name.startswith("."))
        checks.append({"name": "pages", "status": "ok",
                       "message": f"{page_count} wiki pages"})
    except Exception as e:
        checks.append({"name": "pages", "status": "error", "message": str(e)})

    overall = "healthy" if all(c["status"] in ("ok", "warning") for c in checks) else "unhealthy"
    return {
        "status": overall,
        "timestamp": datetime.now().isoformat(),
        "checks": checks,
    }



# ============================================================




# ============================================================

# 7. AI Review System

# ============================================================



def _review_page(page_path: Path) -> dict:

    """Internal: review a draft page using structural + optional AI checks."""

    content = page_path.read_text(encoding="utf-8")

    fm, body = _get_frontmatter(content)

    feedback = []

    passed = True



    if not body or not body.strip():

        feedback.append("Page body is empty")

        passed = False

    else:

        if "## Executive Summary" not in body:

            feedback.append("Missing '## Executive Summary' section")

            passed = False

        else:

            es_match = re.search(r"## Executive Summary\s*\n+(.*?)(?=\n## |\Z)", body, re.DOTALL)

            if not es_match or not es_match.group(1).strip() or es_match.group(1).strip() == "-":

                feedback.append("Executive Summary is empty or placeholder only")

                passed = False



        if "## Key Facts" not in body:

            feedback.append("Missing '## Key Facts' section")

            passed = False

        elif not re.search(r"## Key Facts\s*\n+\s*-\s+\S", body):

            feedback.append("Key Facts has no entries (only '-' placeholder)")

            passed = False



        if "## Relations" not in body:

            feedback.append("Missing '## Relations' section")

            passed = False



        if "## Timeline" not in body:

            feedback.append("Missing '## Timeline' section")

            passed = False



    # Optional AI review if REVIEW_API_KEY is configured

    review_api_key = os.environ.get("REVIEW_API_KEY", "")

    if review_api_key and passed:

        try:

            import urllib.request

            review_api_url = os.environ.get(

                "REVIEW_API_URL",

                "https://open.bigmodel.cn/api/paas/v4/chat/completions"

            )

            review_model = os.environ.get("REVIEW_MODEL", "glm-4-flash")

            review_prompt = (

                'You are a wiki page quality reviewer. Check if the page is substantive.\n'

                'Respond in JSON only: {"passed": true/false, "feedback": ["suggestion1"]}\n\n'

                f'Title: {fm.get("title", "N/A")}\n\n{body[:3000]}'

            )

            req = urllib.request.Request(

                review_api_url,

                data=json.dumps({

                    "model": review_model,

                    "messages": [{"role": "user", "content": review_prompt}],

                    "temperature": 0.1,

                    "max_tokens": 300,

                }).encode("utf-8"),

                headers={

                    "Content-Type": "application/json",

                    "Authorization": f"Bearer {review_api_key}",

                },

                method="POST",

            )

            with urllib.request.urlopen(req, timeout=15) as resp:

                result = json.loads(resp.read().decode("utf-8"))

                ai_content = result["choices"][0]["message"]["content"].strip()

                ai_json = re.sub(r"^```(?:json)?\s*|```$", "", ai_content, flags=re.MULTILINE).strip()

                ai_result = json.loads(ai_json)

                if not ai_result.get("passed", True):

                    passed = False

                    feedback.extend(ai_result.get("feedback", []))

        except Exception as e:

            _logger.warning("AI review failed for %s: %s (using structural check only)", page_path.name, e)



    return {"passed": passed, "feedback": feedback}





@mcp.tool()

def wiki_review(page_id: str) -> str:

    """

    Submit a draft wiki page for AI-assisted quality review.

    If review passes, page status is promoted from 'draft' to 'active'.

    If review fails, returns feedback for improvement -- fix issues and re-submit.

    page_id can be filename (e.g. "hermes-agent") or full relative path.

    Returns {status, passed, feedback}

    """

    page_path = _resolve_page(page_id)

    if not page_path or not page_path.exists():

        return json.dumps({"error": f"Page not found: {page_id}"})



    content = page_path.read_text(encoding="utf-8")

    fm, body = _get_frontmatter(content)

    current_status = fm.get("status", "active")



    if current_status != "draft":

        return json.dumps({

            "status": current_status, "passed": True, "feedback": [],

            "message": f"Page is already '{current_status}', no review needed"

        })



    result = _review_page(page_path)



    if result["passed"]:

        _clear_rejection_history(page_id)
        _update_frontmatter_field(page_path, "status", "active")

        _append_timeline_entry(page_path, "Page reviewed and promoted to active", "wiki_review")

        return json.dumps({

            "status": "active", "passed": True, "feedback": [],

            "message": "Review passed -- page promoted to active"

        })

    else:
        _store_rejection(page_id, "; ".join(result["feedback"]))

        return json.dumps({

            "status": "draft", "passed": False,

            "feedback": result["feedback"],

            "message": "Review failed -- fix the issues and call wiki_review() again"

        })





def _resolve_page(page_id: str):

    """Resolve page_id to a validated Path. Supports filename or relative path."""

    candidate = WIKI_ROOT / page_id

    if candidate.exists():

        return _validate_path(candidate)

    if not page_id.endswith(".md"):

        candidate = WIKI_ROOT / (page_id + ".md")

        if candidate.exists():

            return _validate_path(candidate)

    for subdir in sorted(ALLOWED_SUBDIRS):

        candidate = WIKI_ROOT / subdir / f"{page_id}.md"

        if candidate.exists():

            return _validate_path(candidate)

    return None





def _update_frontmatter_field(page_path: Path, field: str, value: str):

    """Update a single frontmatter field atomically (inside lock)."""

    with _FileLock(page_path):

        content = page_path.read_text(encoding="utf-8")

        fm, body = _get_frontmatter(content)

        fm[field] = value

        import yaml

        fm_yaml = yaml.dump(fm, default_flow_style=None, allow_unicode=True, sort_keys=False).rstrip("\n")

        new_content = f"---\n{fm_yaml}\n---\n{body}"

        _safe_write(page_path, new_content)





def _append_timeline_entry(page_path: Path, event: str, source: str = ""):

    """Append a timeline entry to a wiki page atomically (inside lock)."""

    with _FileLock(page_path):

        content = page_path.read_text(encoding="utf-8")

        fm, body = _get_frontmatter(content)

        now = datetime.now().strftime("%Y-%m-%d")

        entry = f"\n- **{now}** | {event}"

        if source:

            entry += f"\n  [Source: {source}]"

        new_body = body.rstrip() + entry + "\n"

        import yaml

        fm_yaml = yaml.dump(fm, default_flow_style=None, allow_unicode=True, sort_keys=False).rstrip("\n")

        new_content = f"---\n{fm_yaml}\n---\n{new_body}"

        _safe_write(page_path, new_content)



# Entry point
# ============================================================
@mcp.tool()
def wiki_undo(n: int = 1) -> str:
    """Revert the last N [wiki-brain] git commits.
    Only reverts commits with the [wiki-brain] prefix.
    Returns a summary of what was undone."""
    if not (WIKI_ROOT / ".git").exists():
        return "Error: No git repository initialized."
    if n < 1 or n > 20:
        return "Error: n must be between 1 and 20."
    log_result = subprocess.run(
        ["git", "log", f"-{n}", "--oneline", "--grep=\[wiki-brain\]"],
        cwd=str(WIKI_ROOT), capture_output=True, text=True
    )
    commits = [l.strip() for l in log_result.stdout.strip().split("\n") if l.strip()]
    if not commits:
        return "No [wiki-brain] commits to undo."
    result = subprocess.run(
        ["git", "revert", "--no-commit", f"HEAD~{len(commits)}..HEAD"],
        cwd=str(WIKI_ROOT), capture_output=True, text=True
    )
    if result.returncode != 0:
        return f"Error reverting: {result.stderr.strip()}"
    result2 = subprocess.run(
        ["git", "commit", "-m", f"[wiki-brain] undo: revert last {len(commits)} commit(s)"],
        cwd=str(WIKI_ROOT), capture_output=True, text=True
    )
    if result2.returncode != 0:
        return f"Error committing revert: {result2.stderr.strip()}"
    return f"Undone {len(commits)} commit(s):\n" + "\n".join(f"  - {c}" for c in commits)


@mcp.tool()
def wiki_log(limit: int = 10) -> str:
    """Show recent [wiki-brain] git commit history.
    Returns the last N commits with the [wiki-brain] prefix."""
    if not (WIKI_ROOT / ".git").exists():
        return "No git repository initialized."
    result = subprocess.run(
        ["git", "log", f"-{limit}", "--oneline", "--grep=\[wiki-brain\]"],
        cwd=str(WIKI_ROOT), capture_output=True, text=True
    )
    commits = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
    if not commits:
        return "No [wiki-brain] commits found."
    return "Recent wiki changes:\n" + "\n".join(f"  {c}" for c in commits)



if __name__ == "__main__":
    _mcp_port = int(os.environ.get("MCP_PORT", "8764"))
    _mcp_api_key = os.environ.get("MCP_API_KEY", "")

    class _APIKeyMiddleware:
        """Starlette ASGI middleware enforcing MCP_API_KEY."""
        def __init__(self, app, api_key):
            self.app = app
            self.api_key = api_key
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and self.api_key:
                headers = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
                key_hdr = headers.get(b"x-api-key", b"").decode("utf-8", errors="ignore")
                if auth != f"Bearer {self.api_key}" and key_hdr != self.api_key:
                    from starlette.responses import JSONResponse
                    r = JSONResponse({"error": "Unauthorized", "message": "API key required"}, status_code=401)
                    await r(scope, receive, send)
                    return
            await self.app(scope, receive, send)

    if _mcp_api_key:
        _logger.info("MCP API Key authentication ENABLED")
    else:
        _logger.warning(
            "MCP_API_KEY is NOT set - MCP Server has NO authentication! "
            "Port %s is open to all LAN clients.", _mcp_port)

    def _shutdown(signum, frame):
        _logger.info("Signal %s, shutting down...", signum)
        import sys
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    _git_init()
    _logger.info("Wiki Brain MCP Server starting (WIKI_ROOT=%s, port=%s)", WIKI_ROOT, _mcp_port)

    _app = mcp.streamable_http_app()
    if _mcp_api_key:
        _app = _APIKeyMiddleware(_app, _mcp_api_key)
    import uvicorn
    uvicorn.run(_app, host="0.0.0.0", port=_mcp_port)
