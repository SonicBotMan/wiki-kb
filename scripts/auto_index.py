#!/usr/bin/env python3
"""
Scale Assurance — Wiki 新内容自动入库 pipeline。

功能:
1. 检测 ~/wiki/ 下新增/修改的 markdown 文件
2. 自动重新生成 graph.json（知识图谱）
3. 输出需要同步到 OpenViking 的文件列表
4. 可选：自动调用 OpenViking CLI 同步

用法:
  python3 auto_index.py                    # 检查变更 + 重新生成 graph.json
  python3 auto_index.py --sync             # 检查变更 + 生成 graph.json + 同步到 OpenViking
  python3 auto_index.py --force            # 强制重新索引所有文件
  python3 auto_index.py --status           # 显示当前 wiki 状态

Cron 友好: 脚本输出简洁，仅在有变更时输出详细信息。
"""

import json
import os
import re
import sys
import hashlib
import logging
from datetime import datetime
from pathlib import Path

# ============================================================
# 日志配置
# ============================================================
LOG_DIR = Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki"))) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("auto-index")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _fh = logging.FileHandler(LOG_DIR / "auto-index.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_fh)
    _sh = logging.StreamHandler(sys.stderr)
    _sh.setLevel(logging.WARNING)
    _sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _logger.addHandler(_sh)

# Entity Registry + 共享工具
sys.path.insert(0, str(Path(__file__).parent))
try:
    import entity_registry as er
    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False

try:
    from wiki_utils import get_frontmatter
except ImportError:
    get_frontmatter = None

# ============ Configuration ============
WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki")))
CONCEPTS_DIR = WIKI_ROOT / "concepts"
ENTITIES_DIR = WIKI_ROOT / "entities"
PEOPLE_DIR = WIKI_ROOT / "people"
PROJECTS_DIR = WIKI_ROOT / "projects"
MEETINGS_DIR = WIKI_ROOT / "meetings"
IDEAS_DIR = WIKI_ROOT / "ideas"
COMPARISONS_DIR = WIKI_ROOT / "comparisons"
QUERIES_DIR = WIKI_ROOT / "queries"
TOOLS_DIR = WIKI_ROOT / "tools"
RAW_DIR = WIKI_ROOT / "raw"
SRC_ARTICLES_DIR = WIKI_ROOT / "src" / "articles"
DREAM_REPORTS_DIR = WIKI_ROOT / "dream-reports"
AUX_DIRS = [RAW_DIR, SRC_ARTICLES_DIR, DREAM_REPORTS_DIR]
SKIP_INDEX_DIRS = {"raw", "src", "dream-reports"}  # these go into index but in a separate section
INDEX_FILE = WIKI_ROOT / "index.md"
LOG_FILE = WIKI_ROOT / "log.md"
SCHEMA_TYPES = {"entity", "concept", "comparison", "query", "person", "project", "meeting", "idea", "tool", "guide", "meta"}
TYPE_ORDER = ["entities", "concepts", "tools", "people", "projects", "meetings", "ideas", "comparisons", "queries", "dream-reports", "raw", "src/articles"]
TYPE_LABELS = {
    "entities": "Entities", "concepts": "Concepts", "people": "People",
    "projects": "Projects", "meetings": "Meetings", "ideas": "Ideas",
    "tools": "Tools", "comparisons": "Comparisons", "queries": "Queries",
    "dream-reports": "Dream Reports", "raw": "Raw Materials", "src/articles": "Saved Articles",
}
GRAPH_FILE = WIKI_ROOT / "graph.json"
STATE_FILE = WIKI_ROOT / ".auto_index_state.json"
# OpenViking config (REST API — no CLI needed)
OV_BASE_URL = os.environ.get("OPENVIKING_ENDPOINT", "http://localhost:1933")
OV_ACCOUNT = os.environ.get("OPENVIKING_ACCOUNT", "hermes")
OV_USER = os.environ.get("OPENVIKING_USER", "default")
OV_API_KEY = os.environ.get("OPENVIKING_API_KEY", "")


# ============ Wiki Parser ============

if get_frontmatter is not None:
    def parse_frontmatter(content: str) -> dict:
        """Extract YAML frontmatter using shared wiki_utils."""
        fm, _ = get_frontmatter(content)
        return fm
else:
    def parse_frontmatter(content: str) -> dict:
        """Extract YAML frontmatter from markdown using PyYAML (fallback)."""
        import yaml
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
        if not match:
            return {}
        try:
            fm = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return {}
        if not isinstance(fm, dict):
            return {}
        return fm


def parse_relations(content: str) -> list[dict]:
    """Extract Relations table from markdown."""
    relations = []
    in_relations = False
    for line in content.split('\n'):
        if line.strip().startswith('## Relations'):
            in_relations = True
            continue
        if in_relations and line.strip().startswith('## '):
            break
        if in_relations and line.strip().startswith('|') and '---' not in line:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 4:
                target_raw = parts[2].strip()
                # Extract page id from [[wiki-link]] or plain text
                target_id = re.sub(r'\[\[(.+?)\]\]', r'\1', target_raw)
                # Normalize: lowercase, replace spaces with hyphens
                target_id = target_id.lower().replace(' ', '-').replace('_', '-')
                relations.append({
                    "relation": parts[1].strip(),
                    "target": target_id,
                    "note": parts[3] if len(parts) > 3 else "",
                })
    return relations


def file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of file content."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()[:16]


# ============ State Management ============

def load_state() -> dict:
    """Load previous indexing state."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": {}, "last_run": None}


def save_state(state: dict):
    """Save current indexing state."""
    state["last_run"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ============ Change Detection ============

def detect_changes(force: bool = False) -> tuple[list[Path], list[Path], list[Path]]:
    """Detect new, modified, and deleted wiki files.
    
    Returns: (new_files, modified_files, deleted_files)
    """
    state = load_state()
    current_files = {}

    for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR, COMPARISONS_DIR, QUERIES_DIR, TOOLS_DIR] + AUX_DIRS:
        if not subdir.exists():
            continue
        for md_file in subdir.glob("*.md"):
            current_files[str(md_file)] = file_hash(md_file)

    new_files = []
    modified_files = []
    deleted_files = []

    if force:
        # Force mode: treat all files as modified
        for f in current_files:
            modified_files.append(Path(f))
    else:
        for filepath, hash_val in current_files.items():
            if filepath not in state["files"]:
                new_files.append(Path(filepath))
            elif state["files"][filepath] != hash_val:
                modified_files.append(Path(filepath))

        for filepath in state["files"]:
            if filepath not in current_files:
                deleted_files.append(Path(filepath))

    return new_files, modified_files, deleted_files


# ============ Index Generation ============

def generate_index() -> int:
    """Regenerate index.md from all wiki pages. Returns total page count."""
    from datetime import datetime as _dt
    import collections as _col

    pages = _col.defaultdict(list)
    all_subdirs = [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR,
                   MEETINGS_DIR, IDEAS_DIR, COMPARISONS_DIR, QUERIES_DIR, TOOLS_DIR] + AUX_DIRS

    for subdir in all_subdirs:
        if not subdir.exists():
            continue
        type_key = subdir.name  # e.g. "concepts"
        for md_file in sorted(subdir.glob("*.md")):
            name = md_file.stem
            desc = ""
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = parse_frontmatter(content)
                desc = fm.get("description", "") or fm.get("title", "") or ""
            except Exception:
                pass
            if not desc:
                # Extract first meaningful line from body
                try:
                    content = md_file.read_text(encoding="utf-8", errors="replace")[:3000]
                    body = re.sub(r"^---\s*\n.*?\n---", "", content, count=1, flags=re.DOTALL).strip()
                    for line in body.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#") and not line.startswith(">") and not line.startswith("-"):
                            desc = line[:150]
                            break
                except Exception:
                    pass
            pages[type_key].append((name, desc))

    total = sum(len(v) for v in pages.values())
    lines = [
        "# Wiki Index\n\n",
        f"> Content catalog. Every wiki page listed under its type with a one-line summary.\n",
        f"> Read this first to find relevant pages for any query.\n",
        f"> Last updated: {_dt.now().strftime('%Y-%m-%d')} | Total pages: {total}\n\n",
    ]

    for type_key in TYPE_ORDER:
        entries = pages.get(type_key, [])
        if not entries:
            continue
        label = TYPE_LABELS.get(type_key, type_key.capitalize())
        lines.append(f"## {label}\n\n")
        lines.append("<!-- Alphabetical within section -->\n\n")
        for name, desc in sorted(entries, key=lambda x: x[0].lower()):
            if desc:
                lines.append(f"- [[{name}]] — {desc}\n")
            else:
                lines.append(f"- [[{name}]]\n")
        lines.append("\n")

    INDEX_FILE.write_text("".join(lines), encoding="utf-8")
    _logger.info("Index regenerated: %d pages", total)
    return total


# ============ Schema Consistency Check ============

def check_schema_consistency() -> list:
    """Check all wiki pages for schema violations. Returns list of issues."""
    issues = []
    all_subdirs = [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR,
                   MEETINGS_DIR, IDEAS_DIR, COMPARISONS_DIR, QUERIES_DIR, TOOLS_DIR] + AUX_DIRS

    for subdir in all_subdirs:
        if not subdir.exists():
            continue
        for md_file in sorted(subdir.glob("*.md")):
            # Use relative path from wiki root for aux dirs
            rel = str(md_file.relative_to(WIKI_ROOT))
            try:
                content = md_file.read_text(encoding="utf-8")
                fm = parse_frontmatter(content)
            except Exception:
                issues.append(f"YAML_ERROR: {rel}")
                continue

            t = fm.get("type")
            if t and t not in SCHEMA_TYPES:
                issues.append(f"BAD_TYPE({t}): {rel}")

            # Relaxed requirements for auxiliary dirs (raw articles, saved pages, reports)
            is_aux = rel.startswith("raw/") or rel.startswith("src/") or rel.startswith("dream-reports/")
            if is_aux:
                required = ("title", "type")
            else:
                required = ("title", "created", "type", "tags", "sources", "status")

            for field in required:
                if field not in fm:
                    issues.append(f"NO_{field.upper()}: {rel}")

            if "description" not in fm:
                issues.append(f"NO_DESCRIPTION: {rel}")

    return issues


def append_log(message: str):
    """Append a timestamped entry to log.md."""
    from datetime import datetime as _dt
    timestamp = _dt.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## [{timestamp}] auto_index\n{message}\n"
    if LOG_FILE.exists():
        existing = LOG_FILE.read_text(encoding="utf-8")
        LOG_FILE.write_text(existing + entry, encoding="utf-8")
    else:
        LOG_FILE.write_text(f"# Wiki Change Log\n{entry}", encoding="utf-8")
    _logger.info("Log updated: %s", message[:80])


# ============ Graph Generation ============

def generate_graph() -> dict:
    """Generate knowledge graph from all wiki pages."""
    nodes = []
    edges = []
    all_page_ids = set()

    for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR, COMPARISONS_DIR, QUERIES_DIR, TOOLS_DIR] + AUX_DIRS:
        if not subdir.exists():
            continue
        for md_file in subdir.glob("*.md"):
            try:
                content = md_file.read_text(encoding='utf-8')
                fm = parse_frontmatter(content)
                page_id = md_file.stem
                page_type = fm.get("type", subdir.name.rstrip('s'))

                # Normalize tags
                tags = fm.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(',')]

                all_page_ids.add(page_id)
                nodes.append({
                    "id": page_id,
                    "title": fm.get("title", page_id),
                    "type": page_type,
                    "tags": tags,
                    "status": "unknown",
                    "updated": "unknown",
                })

                # Parse dates from frontmatter
                dates = fm.get("dates", {})
                if isinstance(dates, dict):
                    for node in nodes:
                        if node["id"] == page_id:
                            node["updated"] = dates.get("updated", "unknown")

                # Extract compiled-truth status
                ct = fm.get("compiled-truth", {})
                if isinstance(ct, dict):
                    for node in nodes:
                        if node["id"] == page_id:
                            node["status"] = ct.get("status", "unknown")

                # Parse relations
                relations = parse_relations(content)
                for rel in relations:
                    target = rel["target"]
                    if target in all_page_ids:
                        edges.append({
                            "source": page_id,
                            "target": target,
                            "relation": rel["relation"],
                            "note": rel["note"],
                        })

            except Exception as e:
                print(f"⚠ Failed to process {md_file}: {e}")

    # Validate edges: remove self-loops and dangling references
    valid_ids = {n["id"] for n in nodes}
    valid_edges = []
    for edge in edges:
        if edge["source"] == edge["target"]:
            continue  # skip self-loops
        if edge["target"] not in valid_ids:
            continue  # skip dangling references (or keep with warning)
        valid_edges.append(edge)

    return {"nodes": nodes, "edges": valid_edges, "metadata": {
        "generated": datetime.now().isoformat(),
        "node_count": len(nodes),
        "edge_count": len(valid_edges),
    }}


# ============ OpenViking Sync ============

def sync_to_openviking(files: list[Path]) -> bool:
    """Sync changed files to OpenViking via REST API (temp_upload + add_resource)."""
    if not OV_BASE_URL:
        print("⚠ OPENVIKING_ENDPOINT not configured, skipping sync")
        return False

    import urllib.request
    import urllib.error
    import json as _json
    import uuid

    headers = {"Content-Type": "application/json"}
    if OV_API_KEY:
        headers["X-API-Key"] = OV_API_KEY
    if OV_ACCOUNT:
        headers["X-OpenViking-Account"] = OV_ACCOUNT
    if OV_USER:
        headers["X-OpenViking-User"] = OV_USER

    ok_count = 0
    fail_count = 0

    for filepath in files:
        print(f"  📤 Importing {filepath.name} to OpenViking...")
        try:
            # Step 1: temp_upload via multipart/form-data
            content = filepath.read_bytes()
            boundary = uuid.uuid4().hex
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

            upload_req = urllib.request.Request(
                f"{OV_BASE_URL}/api/v1/resources/temp_upload",
                data=body,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    **{k: v for k, v in headers.items() if k != "Content-Type"},
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(upload_req, timeout=60) as resp:
                    upload_data = _json.loads(resp.read())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:200]
                print(f"    ✗ {filepath.name}: upload failed ({e.code}): {err_body}")
                fail_count += 1
                continue

            # Try both key names (API may use temp_file_id or temp_id)
            temp_id = upload_data.get("result", {}).get("temp_file_id") or \
                      upload_data.get("result", {}).get("temp_id")
            if not temp_id:
                print(f"    ✗ {filepath.name}: no temp_id in response: {upload_data}")
                fail_count += 1
                continue

            # Step 2: add_resource
            add_payload = _json.dumps({
                "temp_file_id": temp_id,
                "reason": "auto_index sync",
            }).encode()
            add_req = urllib.request.Request(
                f"{OV_BASE_URL}/api/v1/resources",
                data=add_payload,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(add_req, timeout=60) as resp:
                    add_data = _json.loads(resp.read())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:200]
                print(f"    ✗ {filepath.name}: add_resource failed ({e.code}): {err_body}")
                fail_count += 1
                continue

            status = add_data.get("status")
            if status == "ok":
                print(f"    ✓ {filepath.name}")
                _logger.debug("  ✓ %s", filepath.name)
                ok_count += 1
            else:
                err = add_data.get("error", {}).get("message", "unknown")
                print(f"    ✗ {filepath.name}: {err}")
                _logger.error("  ✗ %s: %s", filepath.name, err)
                fail_count += 1

        except Exception as e:
            print(f"    ✗ {filepath.name}: {e}")
            _logger.error("  ✗ %s: %s", filepath.name, e)
            fail_count += 1

    print(f"  📊 Sync complete: {ok_count} ok, {fail_count} failed")
    return fail_count == 0


# ============ Status Display ============

def show_status(pages_new, pages_modified, pages_deleted):
    """Display wiki indexing status."""
    state = load_state()
    graph = None
    if GRAPH_FILE.exists():
        try:
            graph = json.loads(GRAPH_FILE.read_text())
        except json.JSONDecodeError:
            pass

    print("📊 Wiki Scale Assurance Status")
    print("=" * 40)
    print(f"  Wiki root:    {WIKI_ROOT}")
    print(f"  Last run:     {state.get('last_run', 'Never')}")
    print(f"  Tracked files: {len(state.get('files', {}))}")

    if graph:
        meta = graph.get("metadata", {})
        print(f"  Graph nodes:  {meta.get('node_count', len(graph.get('nodes', [])))}")
        print(f"  Graph edges:  {meta.get('edge_count', len(graph.get('edges', [])))}")

    total_changes = len(pages_new) + len(pages_modified) + len(pages_deleted)
    print(f"\n  Changes since last run: {total_changes}")
    if pages_new:
        print(f"    🆕 New: {len(pages_new)}")
        for f in pages_new:
            print(f"       + {f.name}")
    if pages_modified:
        print(f"    ✏️  Modified: {len(pages_modified)}")
        for f in pages_modified:
            print(f"       ~ {f.name}")
    if pages_deleted:
        print(f"    🗑️  Deleted: {len(pages_deleted)}")
        for f in pages_deleted:
            print(f"       - {f.name}")

    if total_changes == 0:
        print("    ✅ All up to date!")


# ============ Main ============

def main():
    args = set(sys.argv[1:])
    do_sync = "--sync" in args
    force = "--force" in args
    status_only = "--status" in args

    _logger.info("=== auto_index started (sync=%s, force=%s) ===", do_sync, force)

    # Detect changes
    pages_new, pages_modified, pages_deleted = detect_changes(force=force)

    if status_only:
        show_status(pages_new, pages_modified, pages_deleted)
        return

    total_changes = len(pages_new) + len(pages_modified) + len(pages_deleted)

    if total_changes > 0 or force:
        print(f"📋 Detected {total_changes} changes "
              f"(+{len(pages_new)} ~{len(pages_modified)} -{len(pages_deleted)})")
        _logger.info("Detected %d changes: +%d ~%d -%d", total_changes, len(pages_new), len(pages_modified), len(pages_deleted))

    # Update Entity Registry if there are changes
    if HAS_REGISTRY and (total_changes > 0 or force):
        print("📋 Updating Entity Registry...")
        try:
            result = er.scan_wiki_pages(str(WIKI_ROOT))
            print(f"   Registry: {result.get('registered', 0)} new, "
                  f"{result.get('updated', 0)} updated, "
                  f"{result.get('skipped', 0)} skipped")
            _logger.info("Registry: %d new, %d updated, %d skipped",
                         result.get('registered', 0), result.get('updated', 0), result.get('skipped', 0))
        except Exception as e:
            print(f"   ⚠ Registry update failed: {e}")
            _logger.error("Registry update failed: %s", e, exc_info=True)

    # Always regenerate graph (it's cheap)
    print("🔗 Generating knowledge graph...")
    graph = generate_graph()
    GRAPH_FILE.write_text(json.dumps(graph, ensure_ascii=False, indent=2))
    print(f"   ✓ {graph['metadata']['node_count']} nodes, "
          f"{graph['metadata']['edge_count']} edges → {GRAPH_FILE}")
    _logger.info("Graph: %d nodes, %d edges", graph['metadata']['node_count'], graph['metadata']['edge_count'])

    # Always regenerate index.md
    print("📖 Regenerating index.md...")
    page_count = generate_index()
    print(f"   ✓ {page_count} pages → index.md")

    # Sync to OpenViking if requested
    changed_files = pages_new + pages_modified
    sync_ok = True
    if do_sync and changed_files:
        print(f"\n📤 Syncing {len(changed_files)} files to OpenViking...")
        _logger.info("Syncing %d files to OpenViking...", len(changed_files))
        sync_ok = sync_to_openviking(changed_files)
    elif do_sync and not changed_files and not force:
        print("\n✅ No files to sync")
        _logger.info("No files to sync")

    # Schema consistency check — ALWAYS runs
    print("🔍 Checking schema consistency...")
    issues = check_schema_consistency()
    if issues:
        warnings = [i for i in issues if not i.startswith("NO_DESCRIPTION") and not i.startswith("NO_HASH")]
        desc_missing = sum(1 for i in issues if i.startswith("NO_DESCRIPTION"))
        print(f"   ⚠ {len(issues)} issues ({len(warnings)} warnings, {desc_missing} missing descriptions)")
        if warnings:
            for w in warnings[:10]:
                print(f"     - {w}")
            if len(warnings) > 10:
                print(f"     ... and {len(warnings) - 10} more")
        _logger.info("Schema check: %d issues", len(issues))
    else:
        print("   ✓ All pages pass schema check")

    # Append to log.md — only when there are changes
    if total_changes > 0 or force:
        if pages_new:
            new_names = [p.stem for p in pages_new]
            append_log(f"- New pages: {len(pages_new)}\n" + "\n".join(f"  - {n}" for n in new_names))
        if pages_modified:
            mod_names = [p.stem for p in pages_modified]
            append_log(f"- Modified pages: {len(pages_modified)}\n" + "\n".join(f"  - {n}" for n in mod_names))
        if pages_deleted:
            del_names = [p.stem for p in pages_deleted]
            append_log(f"- Deleted pages: {len(pages_deleted)}\n" + "\n".join(f"  - {n}" for n in del_names))

    # Update state (only if sync succeeded or no sync requested)
    if sync_ok:
        state = load_state()
        for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR, COMPARISONS_DIR, QUERIES_DIR, TOOLS_DIR] + AUX_DIRS:
            if not subdir.exists():
                continue
            for md_file in subdir.glob("*.md"):
                state["files"][str(md_file)] = file_hash(md_file)
        save_state(state)
        _logger.info("State saved. last_run=%s", state.get("last_run"))
    else:
        _logger.warning("Sync failed, state NOT updated - files will retry next run")

    if total_changes == 0 and not force:
        print("✅ Wiki is up to date. No changes needed.")
        return


if __name__ == "__main__":
    main()
