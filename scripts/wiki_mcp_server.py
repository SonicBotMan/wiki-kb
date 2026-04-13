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
from pathlib import Path
from datetime import datetime
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
EXCLUDE_DIRS = {
    "logs", "scripts", "queries", "comparisons", "raw", "src"
}
REGISTRY_FILE = WIKI_ROOT / "registry.json"

# OpenViking API 配置
OPENVIKING_HOST = os.environ.get("OPENVIKING_HOST", "localhost")
OPENVIKING_PORT = int(os.environ.get("OPENVIKING_PORT", "1933"))
OPENVIKING_URL = f"http://{OPENVIKING_HOST}:{OPENVIKING_PORT}"
OPENVIKING_API_KEY = os.environ.get("OPENVIKING_API_KEY", "")
OPENVIKING_ACCOUNT = os.environ.get("OPENVIKING_ACCOUNT", "hermes")
OPENVIKING_USER = os.environ.get("OPENVIKING_USER", "default")

# API Key 认证配置
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")

# ============================================================
# 3. 导入 wiki_utils 工具模块
# ============================================================
sys.path.insert(0, str(WIKI_ROOT / "scripts"))
try:
    import wiki_utils
except ImportError as e:
    print(f"警告: 无法导入 wiki_utils 模块: {e}", file=sys.stderr)
    # 提供内联 stub
    def _get_frontmatter(content: str):
        match = re.match(r'^---\n(.*?)\n---\n(.*)$', content, re.DOTALL)
        if match:
            fm_text = match.group(1)
            body = match.group(2)
            fm = {}
            for line in fm_text.split('\n'):
                m = re.match(r'^(\w+):\s*(.*)$', line)
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip("'\"")
            return fm, body
        return {}, content

    def _update_frontmatter(content: str, updates: dict) -> str:
        fm, body = _get_frontmatter(content)
        fm.update(updates)
        fm_lines = [f"{k}: {v}" for k, v in fm.items()]
        return f"---\n" + "\n".join(fm_lines) + "\n---\n" + body

    class wiki_utils:
        get_frontmatter = staticmethod(_get_frontmatter)
        update_frontmatter = staticmethod(_update_frontmatter)
        parse_frontmatter = staticmethod(lambda c: _get_frontmatter(c)[0])
        format_frontmatter = staticmethod(lambda d: "---\n" + "\n".join(f"{k}: {v}" for k, v in d.items()) + "\n---")
        get_frontmatter_field = staticmethod(lambda c, f: _get_frontmatter(c)[0].get(f, ""))
        parse_tags = staticmethod(lambda s: [])

# ============================================================
# 4. 导入 entity_registry 模块
# ============================================================
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
# 5. API Key 认证检查
# ============================================================

def _check_auth() -> bool:
    """
    检查 API Key 认证。
    如果 MCP_API_KEY 未设置，则跳过认证。
    通过检查 Authorization header 进行验证。
    
    注意: 在 FastMCP StreamableHTTP 模式下，我们需要通过其他方式获取 header。
    这里通过检查 environ 中的 HTTP_AUTHORIZATION 来获取。
    """
    if not MCP_API_KEY:
        # 未配置 API Key，跳过认证
        return True
    
    # 从环境变量获取 Authorization header（StreamableHTTP 会设置）
    auth_header = os.environ.get("HTTP_AUTHORIZATION", "")
    
    if not auth_header:
        return False
    
    # 解析 "Bearer <key>" 格式
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token == MCP_API_KEY:
            return True
    
    return False


def _require_auth():
    """如果认证失败则抛出异常"""
    if not _check_auth():
        raise PermissionError("Invalid or missing API Key")

# ============================================================
# 6. OpenViking 健康检查
# ============================================================

# OpenViking 状态缓存
_openviking_healthy = None
_openviking_check_time = 0


def check_openviking_health() -> bool:
    """
    检查 OpenViking 服务是否可达。
    使用简单的 HTTP GET 请求到 health endpoint。
    缓存结果 30 秒避免频繁检查。
    
    Returns:
        True if OpenViking is reachable, False otherwise
    """
    global _openviking_healthy, _openviking_check_time
    
    import time
    current_time = time.time()
    
    # 缓存 30 秒
    if _openviking_healthy is not None and (current_time - _openviking_check_time) < 30:
        return _openviking_healthy
    
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{OPENVIKING_URL}/health",
            method="GET",
            headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                _openviking_healthy = True
            else:
                _openviking_healthy = False
    except Exception:
        _openviking_healthy = False
    
    _openviking_check_time = current_time
    return _openviking_healthy


def get_openviking_status() -> Dict[str, Any]:
    """获取 OpenViking 连通状态详情"""
    is_healthy = check_openviking_health()
    return {
        "reachable": is_healthy,
        "url": OPENVIKING_URL,
        "status": "healthy" if is_healthy else "unreachable"
    }

# ============================================================
# 7. Wiki 工具函数
# ============================================================

def _slugify(name: str) -> str:
    """将名称转换为合法的文件名 slug"""
    name = name.lower().strip()
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[\s_]+', '-', name)
    name = re.sub(r'-+', '-', name)
    return name.strip('-')


def _resolve_page_path(page_id: str) -> Optional[Path]:
    """解析 page_id 为完整路径"""
    page_id = page_id.strip()

    # 如果已经是完整路径
    if '/' in page_id:
        path = WIKI_ROOT / page_id
        if path.exists():
            return path
        # 尝试加 .md
        path = WIKI_ROOT / f"{page_id}.md"
        if path.exists():
            return path

    # 搜索所有子目录
    for subdir in ['concepts', 'entities', 'people', 'projects', 'meetings', 'ideas', 'comparisons', 'queries']:
        path = WIKI_ROOT / subdir / f"{page_id}.md"
        if path.exists():
            return path
        # 模糊匹配
        if '-' in page_id or '_' in page_id:
            for f in (WIKI_ROOT / subdir).glob("*"):
                if f.stem.replace('-', '').replace('_', '') == page_id.replace('-', '').replace('_', ''):
                    return f

    # 搜索所有 .md 文件
    for f in (f for f in WIKI_ROOT.rglob("*.md") if not any(p in f.parts for p in EXCLUDE_DIRS)):
        if f.stem == page_id or f.stem.replace('-', '') == page_id.replace('-', ''):
            return f

    return None


def _get_frontmatter(content: str):
    """兼容性别名，调用 wiki_utils"""
    return wiki_utils.get_frontmatter(content)


def _update_frontmatter(content: str, updates: dict) -> str:
    """兼容性别名，调用 wiki_utils"""
    return wiki_utils.update_frontmatter(content, updates)


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

    # 检查 OpenViking 是否可用
    openviking_available = check_openviking_health()

    if not openviking_available:
        # 如果 OpenViking 不可用，直接使用 fallback
        return _fallback_file_search(query, type_filter)

    try:
        url = f"{OPENVIKING_URL}/api/v1/search/search"
        data = json.dumps({"query": query, "type": type_filter}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENVIKING_API_KEY}",
                "X-OpenViking-Account": OPENVIKING_ACCOUNT,
                "X-OpenViking-User": OPENVIKING_USER,
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            resources = result.get("result", {}).get("resources", [])
            return [{
                "uri": r.get("uri", ""),
                "abstract": r.get("abstract", ""),
                "score": r.get("score", 0)
            } for r in resources]

    except Exception as e:
        # 如果 OpenViking 调用失败，fallback 到文件搜索
        return _fallback_file_search(query, type_filter)


def _fallback_file_search(query: str, type_filter: str = "") -> List[Dict]:
    """当 OpenViking 不可用时，使用文件搜索 fallback"""
    results = []
    query_lower = query.lower()

    for md_file in (md_file for md_file in WIKI_ROOT.rglob("*.md") if not any(p in md_file.parts for p in EXCLUDE_DIRS)):
        # 跳过非内容文件
        if md_file.name in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']:
            continue

        try:
            content = md_file.read_text(encoding='utf-8')
            fm, body = wiki_utils.get_frontmatter(content)

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
                    "summary": body[:200].replace('\n', ' ').strip() if body else "",
                    "fallback_mode": True
                })
        except Exception:
            continue

    return results


# ============================================================
# 8. 创建 FastMCP Server
# ============================================================
# Patch: Set session_idle_timeout BEFORE FastMCP() constructor creates the session manager
import mcp.server.streamable_http_manager as _shm
import mcp.server.fastmcp.server as _fm
_orig_shm_cls = _shm.StreamableHTTPSessionManager
class _PatchedSHM(_orig_shm_cls):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("session_idle_timeout", 86400)  # 24 hours
        super().__init__(*args, **kwargs)
# Patch BOTH modules (streamable_http_manager + fastmcp.server local import)
_shm.StreamableHTTPSessionManager = _PatchedSHM
_fm.StreamableHTTPSessionManager = _PatchedSHM

mcp = FastMCP("wiki-brain", host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "8764")))


# ============================================================
# 9. Wiki 操作 Tools
# ============================================================

@mcp.tool()
def wiki_search(query: str, type: str = "") -> List[Dict]:
    """
    语义搜索 wiki 页面。
    调用 OpenViking search API，支持按 type 过滤。
    如果 OpenViking 不可用，自动使用 fallback 文件搜索。
    返回 [{title, type, page_path, summary, fallback_mode}]
    """
    # 认证检查
    _require_auth()
    
    results = _openviking_search(query, type)
    
    # 如果使用 fallback，添加标注
    openviking_ok = check_openviking_health()
    if not openviking_ok:
        for r in results:
            r["fallback_mode"] = True
            r["message"] = "(fallback mode - OpenViking unavailable)"
    
    return results


@mcp.tool()
def wiki_get(page_id: str) -> Dict:
    """
    读取 wiki 页面完整内容。
    page_id 可以是文件名（如 "hermes-agent"）或完整路径。
    返回 {title, type, frontmatter, executive_summary, key_facts, relations, timeline}
    """
    # 认证检查
    _require_auth()
    
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"页面未找到: {page_id}")

    content = path.read_text(encoding='utf-8')
    fm, body = wiki_utils.get_frontmatter(content)

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
def wiki_create(name: str, type: str, description: str, content: str = "") -> Dict:
    """
    创建新 wiki 页面。
    按 RESOLVER 路由到正确目录，自动生成 frontmatter + v3 schema。
    自动注册到 Entity Registry。
    返回 {page_path, entity_id}
    """
    # 认证检查
    _require_auth()
    
    # 确定目录
    type_dir_map = {
        "person": "people",
        "project": "projects",
        "entity": "entities",
        "concept": "concepts",
        "meeting": "meetings",
        "idea": "ideas",
        "comparison": "comparisons",
        "query": "queries"
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
status: draft
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
    # 认证检查
    _require_auth()
    
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"页面未找到: {page_id}")

    original = path.read_text(encoding='utf-8')
    fm, body = wiki_utils.get_frontmatter(original)

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
    new_content = wiki_utils.format_frontmatter(fm) + "\n" + body

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
    # 认证检查
    _require_auth()
    
    path = _resolve_page_path(page_id)
    if not path:
        raise ValueError(f"页面未找到: {page_id}")

    original = path.read_text(encoding='utf-8')
    fm, body = wiki_utils.get_frontmatter(original)

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
    new_content = wiki_utils.format_frontmatter(fm) + "\n" + body

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
    # 认证检查
    _require_auth()
    
    pages = []

    for md_file in (md_file for md_file in WIKI_ROOT.rglob("*.md") if not any(p in md_file.parts for p in EXCLUDE_DIRS)):
        # 跳过非内容文件
        if md_file.name in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']:
            continue

        try:
            content = md_file.read_text(encoding='utf-8')
            fm, _ = wiki_utils.get_frontmatter(content)

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
# 10. Entity Registry Tools
# ============================================================

@mcp.tool()
def entity_resolve(name: str) -> Optional[Dict]:
    """
    通过名称或别名解析实体。
    返回 {id, canonical_name, aliases, type, primary_page}
    """
    # 认证检查
    _require_auth()
    
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
    # 认证检查
    _require_auth()
    
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
    # 认证检查
    _require_auth()
    
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
    # 认证检查
    _require_auth()
    
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
# 11. 系统 Tool
# ============================================================

@mcp.tool()
def wiki_stats() -> Dict:
    """
    返回 wiki 统计：总页面数、各类型数量、registry 统计等。
    包含 OpenViking 连通状态。
    """
    # 认证检查
    _require_auth()
    
    # 统计页面
    page_counts = {}
    total_pages = 0

    for md_file in (md_file for md_file in WIKI_ROOT.rglob("*.md") if not any(p in md_file.parts for p in EXCLUDE_DIRS)):
        if md_file.name in ['SCHEMA.md', 'RESOLVER.md', 'index.md', 'log.md']:
            continue
        total_pages += 1

        try:
            content = md_file.read_text(encoding='utf-8')
            fm, _ = wiki_utils.get_frontmatter(content)
            ptype = fm.get('type', 'unknown')
            page_counts[ptype] = page_counts.get(ptype, 0) + 1
        except Exception:
            continue

    # Registry 统计
    reg = entity_registry.load_registry()
    reg_stats = reg.get("stats", {})

    # OpenViking 状态
    openviking_status = get_openviking_status()

    return {
        "total_pages": total_pages,
        "pages_by_type": page_counts,
        "total_entities": reg_stats.get("total_entities", 0),
        "total_aliases": reg_stats.get("total_aliases", 0),
        "registry_version": reg.get("version", 1),
        "openviking": openviking_status
    }


# ============================================================
# 12. 启动 Server
# ============================================================

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
