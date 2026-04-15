#!/usr/bin/env python3
"""
Wiki Brain MCP Server — 暴露 Wiki + Entity Registry 为 MCP tools。
使用 FastMCP + stdio 传输，供 Hermes Agent 调用。
"""

import json
import os
import sys
import re
import uuid
import logging
import signal
from pathlib import Path
from datetime import datetime

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
from typing import Optional, List, Dict, Any

# ============================================================
# 1. 检查 mcp 包是否已安装
# ============================================================
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("=" * 60, file=sys.stderr)
    print("错误: mcp 包未安装", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("请运行以下命令安装:", file=sys.stderr)
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
# 3. 导入 entity_registry 模块
# ============================================================
sys.path.insert(0, str(WIKI_ROOT / "scripts"))
try:
    import entity_registry
except ImportError as e:
    print(f"警告: 无法导入 entity_registry 模块: {e}", file=sys.stderr)
    # 提供一个 stub
    class entity_registry:
        @staticmethod
        def load_registry():
            if REGISTRY_FILE.exists():
                with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {"entities": {}, "alias_index": {}, "page_index": {}, "stats": {}}

        @staticmethod
        def save_registry(reg):
            REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = REGISTRY_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(reg, f, ensure_ascii=False, indent=2)
            tmp.rename(REGISTRY_FILE)

        @staticmethod
        def resolve(name):
            reg = entity_registry.load_registry()
            norm = name.lower().strip()
            entity_id = reg["alias_index"].get(norm)
            if entity_id:
                return reg["entities"].get(entity_id)
            for e in reg["entities"].values():
                if e.get("canonical_name", "").lower().strip() == norm:
                    return e
            return None

        @staticmethod
        def register(name, entity_type, page_path="", aliases=None, reg=None):
            should_save = reg is None
            if reg is None:
                reg = entity_registry.load_registry()
            now = datetime.now().strftime("%Y-%m-%d")
            entity_id = f"ent_{uuid.uuid4().hex[:6]}"
            new_entity = {
                "id": entity_id,
                "canonical_name": name.strip(),
                "aliases": aliases or [],
                "type": entity_type,
                "primary_page": page_path,
                "related_pages": [],
                "created": now,
                "updated": now,
                "metadata": {}
            }
            reg["entities"][entity_id] = new_entity
            if page_path:
                reg["page_index"][page_path] = entity_id
            reg["stats"]["total_entities"] = len(reg["entities"])
            if should_save:
                entity_registry.save_registry(reg)
            return new_entity

        @staticmethod
        def merge(entity_id, into_id):
            reg = entity_registry.load_registry()
            if entity_id not in reg["entities"] or into_id not in reg["entities"]:
                return False
            target = reg["entities"][into_id]
            source = reg["entities"][entity_id]
            for alias in source.get("aliases", []):
                if alias not in target["aliases"]:
                    target["aliases"].append(alias)
                    reg["alias_index"][alias] = into_id
            for page in source.get("related_pages", []):
                if page not in target["related_pages"]:
                    target["related_pages"].append(page)
            if source.get("primary_page"):
                target["related_pages"].append(source["primary_page"])
            target["updated"] = datetime.now().strftime("%Y-%m-%d")
            for alias in source.get("aliases", []):
                if reg["alias_index"].get(alias) == entity_id:
                    del reg["alias_index"][alias]
            del reg["entities"][entity_id]
            entity_registry.save_registry(reg)
            return True

        @staticmethod
        def get_all_entities():
            reg = entity_registry.load_registry()
            return list(reg["entities"].values())

# ============================================================
# 4. 路径安全工具
# ============================================================

# 允许的 wiki 子目录白名单
_ALLOWED_SUBDIRS = {'concepts', 'entities', 'people', 'projects', 
                    'meetings', 'ideas', 'comparisons', 'queries', 'tools'}
# 允许的 entity/page 类型
_ALLOWED_TYPES = {'person', 'project', 'entity', 'concept', 
                  'meeting', 'idea', 'comparison', 'query', 'tool'}



# 扫描时排除的目录（系统产物、原始文件，非 wiki 内容）
_EXCLUDE_DIRS = {'dream-reports', 'raw', 'src', 'logs', 'scripts', '.git',
                 'assets', 'papers', 'transcripts', 'articles',
                 'images', 'pdfs', 'videos', 'audio'}


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
            raise ValueError(f"路径越界: {path} 不在 WIKI_ROOT 内")
        return resolved
    except (OSError, ValueError) as e:
        raise ValueError(f"无效路径: {e}")


def _validate_type(entity_type: str) -> str:
    """验证 entity/page 类型是否合法。"""
    normalized = entity_type.lower().strip()
    if normalized not in _ALLOWED_TYPES:
        raise ValueError(f"无效类型: '{entity_type}'，允许: {_ALLOWED_TYPES}")
    return normalized


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
    for subdir in ['concepts', 'entities', 'people', 'projects', 'meetings', 'ideas', 'comparisons', 'queries', 'tools']:
        path = WIKI_ROOT / subdir / f"{page_id}.md"
        if path.exists():
            return _validate_path(path)
        # 模糊匹配
        if '-' in page_id or '_' in page_id:
            for f in (WIKI_ROOT / subdir).glob("*"):
                if f.stem.replace('-', '').replace('_', '') == page_id.replace('-', '').replace('_', ''):
                    return f

    # 搜索所有 .md 文件
    for f in WIKI_ROOT.rglob("*.md"):
        if _is_excluded(f):
            continue
        if f.stem == page_id or f.stem.replace('-', '') == page_id.replace('-', ''):
            return f

    return None


def _get_frontmatter(content: str) -> tuple[dict, str]:
    """提取 markdown 文件的 frontmatter"""
    match = re.match(r'^---\n(.*?)\n---\n(.*)$', content, re.DOTALL)
    if match:
        fm_text = match.group(1)
        body = match.group(2)
        fm = {}
        for line in fm_text.split('\n'):
            m = re.match(r'^(\w+):\s*(.*)$', line)
            if m:
                key, val = m.group(1), m.group(2)
                val = val.strip().strip("'\"")
                fm[key] = val
        return fm, body
    return {}, content


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

        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            # Parse OpenViking v1 response format
            if result.get("status") == "ok":
                items = []
                for r in (result.get("result", {}).get("resources") or []):
                    uri = r.get("uri", "")
                    # 从 URI 提取 title: viking://resources/<name>/<file>
                    parts = uri.rstrip("/").split("/")
                    title = parts[-2] if len(parts) >= 2 else parts[-1]
                    title = title.replace("_", " ")
                    items.append({
                        "title": title,
                        "type": "",
                        "page_path": "",
                        "summary": r.get("abstract", ""),
                    })
                _logger.info("openviking_search: query=%r type=%r → %d results", query, type_filter, len(items))
                return items
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
        except Exception:
            continue

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
        raise ValueError(f"页面未找到: {page_id}")

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
        "timeline": timeline,
        "raw_body": body
    }


@mcp.tool()
def wiki_create(name: str, type: str, description: str, content: str = "", status: str = "active") -> Dict:
    """
    创建新 wiki 页面。
    按 RESOLVER 路由到正确目录，自动生成 frontmatter + v3 schema。
    自动注册到 Entity Registry。
    返回 {page_path, entity_id}
    """
    # 验证 type
    type = _validate_type(type)
    
    # 内容长度限制 (1MB)
    if len(description) > 500_000 or len(content) > 1_000_000:
        raise ValueError("内容过长，description 最大 500KB，content 最大 1MB")
    
    # 确定目录
    type_dir_map = {
        "person": "people",
        "project": "projects",
        "entity": "entities",
        "concept": "concepts",
        "meeting": "meetings",
        "idea": "ideas",
        "comparison": "comparisons",
        "query": "queries",
        "tool": "tools"
    }

    subdir = type_dir_map.get(type.lower(), "concepts")
    slug = _slugify(name)
    page_path = WIKI_ROOT / subdir / f"{slug}.md"

    if page_path.exists():
        raise ValueError(f"页面已存在: {page_path}")

    # 生成 frontmatter
    now = datetime.now().strftime("%Y-%m-%d")
    default_content = content if content else description

    # 构建 v3 schema 结构
    page_content = f"""---
title: {name}
created: {now}
updated: {now}
type: {type}
tags: []
sources: []
status: {status}
---

# {name}

## Executive Summary

{default_content}

## Key Facts

-

## Relations
| 关系 | 目标 | 说明 |
|------|------|------|
| related | [[]] | |

---

## Timeline

- **{now}** | 页面创建
  [Source: wiki_mcp_server]
"""

    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(page_content, encoding='utf-8')

    # 注册到 Entity Registry
    rel_path = str(page_path.relative_to(WIKI_ROOT))
    entity = entity_registry.register(
        name=name,
        entity_type=type,
        page_path=rel_path
    )

    return {
        "page_path": rel_path,
        "entity_id": entity.get("id"),
        "title": name,
        "type": type
    }


@mcp.tool()
def wiki_update(page_id: str, section: str, content: str) -> Dict:
    """
    更新指定 section（executive_summary / key_facts / relations）。
    Timeline section 自动 append，不覆盖。
    返回 {page_path, updated_sections}
    """
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"页面未找到: {page_id}")

    original = path.read_text(encoding='utf-8')
    fm, body = _get_frontmatter(original)

    # 更新 body 中的 section
    if section.lower() == "timeline":
        # Timeline 追加模式
        new_body = body.rstrip()
        if not new_body.endswith('\n'):
            new_body += '\n'
        new_body += f"\n- **{datetime.now().strftime('%Y-%m-%d')}** | {content}\n"
        body = new_body
    else:
        body = _update_section(body, section, content)

    # 更新 frontmatter 中的 updated 日期
    fm['updated'] = datetime.now().strftime("%Y-%m-%d")

    # 重新组装
    fm_lines = [f"{k}: {v}" for k, v in fm.items()]
    new_content = f"---\n" + "\n".join(fm_lines) + "\n---\n" + body

    path.write_text(new_content, encoding='utf-8')

    rel_path = str(path.relative_to(WIKI_ROOT))
    return {
        "page_path": rel_path,
        "updated_sections": [section],
        "section_content": content
    }


@mcp.tool()
def wiki_append_timeline(page_id: str, event: str, source: str = "") -> Dict:
    """
    向指定页面追加 Timeline 条目。
    自动格式化为 `- **YYYY-MM-DD** | event \n  [Source: source]`
    更新 frontmatter updated 日期。
    返回 {page_path, timeline_entry}
    """
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"页面未找到: {page_id}")

    original = path.read_text(encoding='utf-8')
    fm, body = _get_frontmatter(original)

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

    # 重新组装
    fm_lines = [f"{k}: {v}" for k, v in fm.items()]
    new_content = f"---\n" + "\n".join(fm_lines) + "\n---\n" + body

    path.write_text(new_content, encoding='utf-8')

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
        except Exception:
            continue

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
    success = entity_registry.merge(id1, id2)
    if success:
        return {
            "success": True,
            "merged_id": id1,
            "into_id": id2,
            "message": f"实体 {id1} 已合并到 {id2}"
        }
    else:
        return {
            "success": False,
            "error": "合并失败，实体可能不存在"
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
        except Exception:
            continue

    # Registry 统计
    reg = entity_registry.load_registry()
    reg_stats = reg.get("stats", {})

    return {
        "total_pages": total_pages,
        "pages_by_type": page_counts,
        "total_entities": reg_stats.get("total_entities", 0),
        "total_aliases": reg_stats.get("total_aliases", 0),
        "registry_version": reg.get("version", 1)
    }


# ============================================================
# 8.5 Health Check
# ============================================================

@mcp.tool()
def wiki_health() -> Dict:
    """
    健康检查：验证 wiki 核心依赖状态。
    返回 {status, checks: [{name, status, message}]}
    """
    import time
    checks = []

    # 1. Registry 文件完整性
    try:
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                json.load(f)  # 验证 JSON
            checks.append({"name": "registry", "status": "ok", "message": "registry.json 完整"})
        else:
            checks.append({"name": "registry", "status": "ok", "message": "registry.json 不存在（将首次创建）"})
    except (json.JSONDecodeError, IOError) as e:
        checks.append({"name": "registry", "status": "error", "message": f"registry.json 损坏: {e}"})

    # 2. Wiki 目录可写
    try:
        test_file = WIKI_ROOT / ".health_check_tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        checks.append({"name": "disk_writable", "status": "ok", "message": f"可写: {WIKI_ROOT}"})
    except Exception as e:
        checks.append({"name": "disk_writable", "status": "error", "message": str(e)})

    # 3. 磁盘空间
    try:
        stat = os.statvfs(str(WIKI_ROOT))
        free_pct = (stat.f_bavail / stat.f_blocks) * 100
        if free_pct < 10:
            checks.append({"name": "disk_space", "status": "warning", 
                          "message": f"磁盘空间不足: {free_pct:.1f}% 可用"})
        else:
            checks.append({"name": "disk_space", "status": "ok", 
                          "message": f"磁盘空间充足: {free_pct:.1f}% 可用"})
    except Exception as e:
        checks.append({"name": "disk_space", "status": "error", "message": str(e)})

    # 4. OpenViking 连通性
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
                              "message": f"OpenViking 可达 ({ms:.0f}ms)"})
            else:
                checks.append({"name": "openviking", "status": "warning", 
                              "message": f"OpenViking 返回 {resp.status}"})
    except Exception as e:
        checks.append({"name": "openviking", "status": "degraded", 
                      "message": f"OpenViking 不可达: {type(e).__name__}"})

    # 5. 页面文件数
    try:
        page_count = sum(1 for f in WIKI_ROOT.rglob("*.md") 
                        if f.name not in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']
                        and not _is_excluded(f))
        checks.append({"name": "pages", "status": "ok", 
                      "message": f"{page_count} 个 wiki 页面"})
    except Exception as e:
        checks.append({"name": "pages", "status": "error", "message": str(e)})

    # 汇总状态
    statuses = [c["status"] for c in checks]
    if "error" in statuses:
        overall = "unhealthy"
    elif "warning" in statuses or "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "timestamp": datetime.now().isoformat(),
        "checks": checks
    }


# ============================================================
# 9. Graceful Shutdown
# ============================================================
_shutdown_requested = False

def _signal_handler(signum, frame):
    """处理 SIGTERM/SIGINT，确保 in-flight 写入完成。"""
    global _shutdown_requested
    if _shutdown_requested:
        _logger.warning("收到重复关闭信号 (%s)，强制退出", signum)
        sys.exit(1)
    _shutdown_requested = True
    _logger.info("收到信号 %s，优雅关闭中...", signum)
    # FastMCP 的 uvicorn 会在下一次循环检测到 shutdown 标志后自动退出
    sys.exit(0)


# ============================================================
# 10. 启动 Server
# ============================================================
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    _logger.info("Wiki Brain MCP Server 启动 (WIKI_ROOT=%s, port=%s)", 
                WIKI_ROOT, os.environ.get("MCP_PORT", "8764"))
    mcp.run(transport="streamable-http")
