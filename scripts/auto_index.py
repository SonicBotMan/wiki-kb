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
from datetime import datetime
from pathlib import Path

# Entity Registry integration
import sys
sys.path.insert(0, str(Path(__file__).parent))
try:
    import entity_registry as er
    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False

# ============ Configuration ============
WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", str(Path.home() / "wiki")))
CONCEPTS_DIR = WIKI_ROOT / "concepts"
ENTITIES_DIR = WIKI_ROOT / "entities"
PEOPLE_DIR = WIKI_ROOT / "people"
PROJECTS_DIR = WIKI_ROOT / "projects"
MEETINGS_DIR = WIKI_ROOT / "meetings"
IDEAS_DIR = WIKI_ROOT / "ideas"
GRAPH_FILE = WIKI_ROOT / "graph.json"
STATE_FILE = WIKI_ROOT / ".auto_index_state.json"
OPENVIKING_BIN = Path(os.environ.get("OPENVIKING_BIN", ""))

# OpenViking config
OV_BASE_URL = os.environ.get("OPENVIKING_ENDPOINT", "http://localhost:1933")
OV_API_KEY = os.environ.get("OPENVIKING_API_KEY", "")
OV_ACCOUNT = os.environ.get("OPENVIKING_ACCOUNT", "hermes")
OV_USER = os.environ.get("OPENVIKING_USER", "default")


# ============ Wiki Parser ============

def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter."""
    match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).split('\n'):
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',')]
            fm[key] = val
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

    for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR]:
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


# ============ Graph Generation ============

def generate_graph() -> dict:
    """Generate knowledge graph from all wiki pages."""
    nodes = []
    edges = []
    all_page_ids = set()

    for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR]:
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
                    if target in all_page_ids or True:  # include all, validate later
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
    """Sync changed files to OpenViking via two-step REST API (temp_upload + add_resource)."""
    if not OV_API_KEY:
        print("\u26a0 OPENVIKING_API_KEY not set, skipping sync")
        return False

    import urllib.request

    auth_headers = {
        "Authorization": f"Bearer {OV_API_KEY}",
        "X-OpenViking-Account": OV_ACCOUNT,
        "X-OpenViking-User": OV_USER,
    }

    success_count = 0
    for filepath in files:
        print(f"  Syncing {filepath.name} to OpenViking...")
        try:
            # Step 1: temp_upload (multipart/form-data)
            filename = filepath.name
            with open(filepath, "r", encoding="utf-8") as f:
                file_content = f.read().encode("utf-8")

            boundary = "----WikiBrainBoundary7ma4yb4d"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: text/markdown\r\n\r\n"
            ).encode("utf-8") + file_content + f"\r\n--{boundary}--\r\n".encode("utf-8")

            upload_url = f"{OV_BASE_URL}/api/v1/resources/temp_upload"
            upload_req = urllib.request.Request(upload_url, data=body, method="POST", headers={
                **auth_headers,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            })

            with urllib.request.urlopen(upload_req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("status") != "ok":
                    print(f"    FAIL upload: {result}")
                    continue
                temp_file_id = result["result"]["temp_file_id"]

            # Step 2: add_resource with temp_file_id
            resource_url = f"{OV_BASE_URL}/api/v1/resources"
            resource_data = json.dumps({
                "temp_file_id": temp_file_id,
                "parent": "viking://resources/wiki/",
                "wait": False,
            }).encode("utf-8")

            resource_req = urllib.request.Request(resource_url, data=resource_data, method="POST", headers={
                **auth_headers,
                "Content-Type": "application/json",
            })

            with urllib.request.urlopen(resource_req, timeout=90) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("status") == "ok":
                    root_uri = result.get("result", {}).get("root_uri", "")
                    print(f"    OK {filepath.name} -> {root_uri}")
                    success_count += 1
                else:
                    print(f"    FAIL {filepath.name}: {result}")

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:200]
            print(f"    FAIL {filepath.name}: HTTP {e.code} {err_body}")
        except Exception as e:
            print(f"    FAIL {filepath.name}: {e}")

    print(f"  Synced {success_count}/{len(files)} files")
    return success_count > 0
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

    # Detect changes
    pages_new, pages_modified, pages_deleted = detect_changes(force=force)

    if status_only:
        show_status(pages_new, pages_modified, pages_deleted)
        return

    total_changes = len(pages_new) + len(pages_modified) + len(pages_deleted)

    if total_changes > 0 or force:
        print(f"📋 Detected {total_changes} changes "
              f"(+{len(pages_new)} ~{len(pages_modified)} -{len(pages_deleted)})")

    # Update Entity Registry if there are changes
    if HAS_REGISTRY and (total_changes > 0 or force):
        print("📋 Updating Entity Registry...")
        try:
            result = er.scan_wiki_pages(str(WIKI_ROOT))
            print(f"   Registry: {result.get('registered', 0)} new, "
                  f"{result.get('updated', 0)} updated, "
                  f"{result.get('skipped', 0)} skipped")
        except Exception as e:
            print(f"   ⚠ Registry update failed: {e}")

    # Always regenerate graph (it's cheap)
    print("🔗 Generating knowledge graph...")
    graph = generate_graph()
    GRAPH_FILE.write_text(json.dumps(graph, ensure_ascii=False, indent=2))
    print(f"   ✓ {graph['metadata']['node_count']} nodes, "
          f"{graph['metadata']['edge_count']} edges → {GRAPH_FILE}")

    # Sync to OpenViking if requested
    changed_files = pages_new + pages_modified
    if do_sync and changed_files:
        print(f"\n📤 Syncing {len(changed_files)} files to OpenViking...")
        sync_to_openviking(changed_files)
    elif do_sync and not changed_files and not force:
        print("\n✅ No files to sync")

    # Update state
    state = load_state()
    for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR]:
        if not subdir.exists():
            continue
        for md_file in subdir.glob("*.md"):
            state["files"][str(md_file)] = file_hash(md_file)
    save_state(state)

    if total_changes == 0 and not force:
        print("✅ Wiki is up to date. No changes needed.")
        return


if __name__ == "__main__":
    main()
