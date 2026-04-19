#!/usr/bin/env python3
"""
Dream Cycle — 定期 LLM 回顾 Wiki 内容，发现矛盾/过时/缺失，自动更新。

工作流程:
1. 读取所有 wiki markdown 文件
2. 构建知识图谱摘要（标题 + tags + TL;DR + Relations）
3. 调用 LLM 分析：
   - 跨页面矛盾检测
   - 过时信息识别
   - 知识缺口发现
   - Relations 一致性检查
4. 生成 patch 建议（仅更新元数据，不改动正文）
5. 输出报告到 ~/wiki/dream-reports/

依赖: zhipu API (GLM-4-flash), 通过 .env 或环境变量配置

用法:
  python3 dream_cycle.py              # 完整分析
  python3 dream_cycle.py --dry-run    # 仅分析不修改
  python3 dream_cycle.py --apply      # 分析并自动应用更新
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

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
REPORT_DIR = WIKI_ROOT / "dream-reports"
CONCEPTS_DIR = WIKI_ROOT / "concepts"
ENTITIES_DIR = WIKI_ROOT / "entities"
PEOPLE_DIR = WIKI_ROOT / "people"
PROJECTS_DIR = WIKI_ROOT / "projects"
MEETINGS_DIR = WIKI_ROOT / "meetings"
IDEAS_DIR = WIKI_ROOT / "ideas"
SCHEMA_FILE = WIKI_ROOT / "SCHEMA.md"

# LLM 配置
LLM_BASE_URL = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
LLM_MODEL = os.environ.get("DREAM_CYCLE_MODEL", "glm-4-flash")
LLM_API_KEY = os.environ.get("GLM_API_KEY", "")

# ============ Wiki Parser ============

def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown."""
    match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).split('\n'):
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # Handle list values like [tag1, tag2]
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
                relations.append({
                    "relation": parts[1],
                    "target": parts[2],
                    "note": parts[3] if len(parts) > 3 else ""
                })
    return relations


def extract_tldr(content: str) -> str:
    """Extract TL;DR section content (v2 compat)."""
    match = re.search(r'## TL;DR\s*\n(.*?)(?=\n## |\n---|\Z)', content, re.DOTALL)
    return match.group(1).strip() if match else ""


def extract_compiled_truth(content: str) -> str:
    """Extract compiled-truth from v3 Executive Summary or v2 blockquote."""
    # v3: Executive Summary section
    match = re.search(r'## Executive Summary\s*\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
    if match:
        return match.group(1).strip()
    # v2 fallback: blockquote
    match = re.search(r'\*\*compiled-truth\*\*:\s*(.+?)(?:\n|$)', content)
    return match.group(1).strip() if match else ""


def load_all_pages() -> list[dict]:
    """Load all wiki pages with parsed metadata."""
    pages = []
    for subdir in [CONCEPTS_DIR, ENTITIES_DIR, PEOPLE_DIR, PROJECTS_DIR, MEETINGS_DIR, IDEAS_DIR]:
        if not subdir.exists():
            continue
        for md_file in subdir.glob("*.md"):
            try:
                content = md_file.read_text(encoding='utf-8')
                fm = parse_frontmatter(content)
                pages.append({
                    "path": str(md_file),
                    "filename": md_file.name,
                    "id": md_file.stem,
                    "frontmatter": fm,
                    "type": fm.get("type", "unknown"),
                    "title": fm.get("title", md_file.stem),
                    "tags": fm.get("tags", []),
                    "compiled_truth": extract_compiled_truth(content),
                    "tldr": extract_tldr(content),
                    "relations": parse_relations(content),
                    "status": fm.get("status", "active"),
                    "updated": fm.get("dates", {}).get("updated", fm.get("updated", "unknown")) if isinstance(fm.get("dates"), dict) else fm.get("updated", "unknown"),
                    "content_length": len(content),
                })
            except Exception as e:
                print(f"⚠ Failed to parse {md_file}: {e}")
    return pages


def build_knowledge_summary(pages: list[dict]) -> str:
    """Build a condensed knowledge graph summary for LLM analysis."""
    lines = ["# Wiki Knowledge Graph Summary\n"]
    lines.append(f"Total pages: {len(pages)}")
    lines.append(f"Generated: {datetime.now().isoformat()}\n")

    for p in sorted(pages, key=lambda x: x["id"]):
        lines.append(f"## [{p['id']}] {p['title']}")
        lines.append(f"- Type: {p['type']}")
        if p['tags']:
            lines.append(f"- Tags: {', '.join(p['tags'] if isinstance(p['tags'], list) else [p['tags']])}")
        if p['compiled_truth']:
            lines.append(f"- Truth: {p['compiled_truth']}")
        if p['tldr']:
            lines.append(f"- TL;DR: {p['tldr'][:200]}")
        if p['relations']:
            for r in p['relations']:
                lines.append(f"- → {r['relation']} → {r['target']}: {r['note']}")
        lines.append("")

    return "\n".join(lines)


# ============ LLM Analysis ============

def call_llm(prompt: str, system: str = "") -> str:
    """Call GLM API for analysis."""
    import urllib.request
    import urllib.error

    url = f"{LLM_BASE_URL}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            if e.code == 429 and attempt < max_retries - 1:
                wait = (attempt + 1) * 30
                print(f"  Rate limited (429), waiting {wait}s... (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            raise RuntimeError(f"LLM API error {e.code}: {error_body}")


def analyze_knowledge_graph(summary: str) -> dict:
    """Run LLM analysis on the knowledge graph."""
    system_prompt = """你是一个知识库审计专家。分析以下 Wiki 知识图谱，找出问题并给出具体修改建议。

请严格按以下 JSON 格式输出（不要包含 markdown 代码块标记）：
{
  "contradictions": [{"page": "page-id", "issue": "描述", "suggestion": "建议修改"}],
  "outdated": [{"page": "page-id", "field": "updated|status|tags（frontmatter字段名）或 Executive Summary（表示需要更新摘要内容）", "current": "当前值", "suggested": "建议值/建议的新摘要文本", "reason": "原因"}],
  "gaps": [{"topic": "主题", "description": "描述", "priority": "high|medium|low"}],
  "relation_issues": [{"page": "page-id", "issue": "描述", "suggestion": "建议"}],
  "quality_score": {"overall": 1-10, "coverage": 1-10, "consistency": 1-10, "freshness": 1-10},
  "summary": "一句话总结本次审计结果"
}

注意：
- 仅关注事实性错误、逻辑矛盾、信息过时
- 不要建议添加用户未提及的信息
- "outdated" 中的日期检查：对比 "updated" 字段和当前时间
- "relation_issues" 检查：单向引用、悬空链接、循环依赖
- 质量评分 10 分制，综合加权 overall"""

    print("🌙 Dream Cycle: 调用 LLM 分析知识图谱...")
    raw = call_llm(summary, system_prompt)

    # 清理可能的 markdown 代码块包裹
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```\w*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```$', '', cleaned)
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"⚠ LLM 输出解析失败: {e}")
        print(f"Raw output: {raw[:500]}")
        return {"contradictions": [], "outdated": [], "gaps": [],
                "relation_issues": [], "quality_score": {"overall": 0},
                "summary": "LLM 输出解析失败", "raw": raw}


def generate_patches(analysis: dict, pages: list[dict]) -> list[dict]:
    """Convert analysis results into concrete file patches."""
    patches = []
    page_map = {p["id"]: p for p in pages}

    # 处理 outdated 项 — 更新 frontmatter 中的 updated 日期
    for item in analysis.get("outdated", []):
        pid = item.get("page", "")
        if pid in page_map:
            page = page_map[pid]
            patches.append({
                "type": "frontmatter_update",
                "page": pid,
                "path": page["path"],
                "field": item.get("field", "dates.updated"),
                "current": item.get("current", ""),
                "suggested": item.get("suggested", ""),
                "reason": item.get("reason", ""),
            })

    # 处理 relation_issues — 修复 Relations 表
    for item in analysis.get("relation_issues", []):
        pid = item.get("page", "")
        if pid in page_map:
            patches.append({
                "type": "relation_fix",
                "page": pid,
                "path": page_map[pid]["path"],
                "issue": item.get("issue", ""),
                "suggestion": item.get("suggestion", ""),
            })

    return patches


def apply_patch(patch: dict) -> bool:
    """Apply a single patch to a wiki file."""
    path = Path(patch["path"])
    if not path.exists():
        print(f"  ✗ File not found: {path}")
        return False

    content = path.read_text(encoding='utf-8')

    if patch["type"] == "frontmatter_update":
        field = patch["field"]
        new_val = patch["suggested"]
        
        # Handle Executive Summary / Truth updates (not frontmatter fields)
        if field.lower() in ("truth", "executive summary", "compiled-truth", "summary"):
            print(f"  ✓ Updating Executive Summary for {path.name}")
            # Extract summary from new_val (might be the full text or a key: value)
            summary_text = new_val
            if ": " in new_val and len(new_val.split(": ", 1)[1]) > 10:
                summary_text = new_val.split(": ", 1)[1]
            # Use inline update logic
            lines = content.split("\n")
            summary_start = None
            summary_end = None
            for i, line in enumerate(lines):
                if line.strip() == "## Executive Summary":
                    summary_start = i
                elif summary_start is not None and line.strip().startswith("## "):
                    summary_end = i
                    break
            if summary_start is not None:
                if summary_end is None:
                    for i in range(summary_start + 1, len(lines)):
                        if lines[i].strip() == "---":
                            summary_end = i
                            break
                    if summary_end is None:
                        summary_end = len(lines)
                new_lines = lines[:summary_start + 1] + ["", summary_text, ""] + lines[summary_end:]
                content = "\n".join(new_lines)
                today = datetime.now().strftime("%Y-%m-%d")
                content = re.sub(r"^(updated:).*", rf"\1 {today}", content, count=1, flags=re.MULTILINE)
                path.write_text(content, encoding='utf-8')
                print(f"  ✓ Executive Summary updated: {path.name}")
                return True
            else:
                print(f"  ✗ No Executive Summary section found in {path.name}")
                return False
        
        # Original frontmatter field update logic
        if '.' in field:
            parent, child = field.split('.', 1)
            pattern = rf'({parent}:\s*\n\s*{child}:\s*)[^\n]+'
            match = re.search(pattern, content)
            if match:
                content = content[:match.start()] + f"{parent}:\n  {child}: {new_val}" + content[match.end():]
            else:
                print(f"  ✗ Field {field} not found in {path.name}")
                return False
        else:
            pattern = rf'^{re.escape(field)}:\s*.+'
            match = re.search(pattern, content, re.MULTILINE)
            if match:
                content = content[:match.start()] + f"{field}: {new_val}" + content[match.end():]
            else:
                print(f"  ✗ Field {field} not found in {path.name}")
                return False

    elif patch["type"] == "relation_fix":
        # For relation fixes, we just note them — manual review recommended
        print(f"  ⚠ Relation fix needs manual review: {patch['suggestion']}")
        return False

    path.write_text(content, encoding='utf-8')
    print(f"  ✓ Patched {path.name}: {patch['field']} → {patch['suggested']}")
    return True


# ============ Report Generation ============

def save_report(analysis: dict, patches: list[dict], applied: bool = False):
    """Save analysis report to dream-reports/."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_file = REPORT_DIR / f"dream-{timestamp}.json"

    report = {
        "timestamp": datetime.now().isoformat(),
        "model": LLM_MODEL,
        "analysis": analysis,
        "patches": patches,
        "applied": applied,
    }

    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"📄 Report saved: {report_file}")

    # Also save a human-readable summary
    summary_file = REPORT_DIR / f"dream-{timestamp}.md"
    score = analysis.get("quality_score", {})
    lines = [
        f"# Dream Cycle Report — {timestamp}",
        f"",
        f"**Model**: {LLM_MODEL}",
        f"**Overall Score**: {score.get('overall', 'N/A')}/10",
        f"**Coverage**: {score.get('coverage', 'N/A')}/10",
        f"**Consistency**: {score.get('consistency', 'N/A')}/10",
        f"**Freshness**: {score.get('freshness', 'N/A')}/10",
        f"",
        f"## Summary",
        f"{analysis.get('summary', 'N/A')}",
        f"",
    ]

    if analysis.get("contradictions"):
        lines.append("## ⚠ Contradictions")
        for c in analysis["contradictions"]:
            lines.append(f"- **[{c['page']}]** {c['issue']}")
            lines.append(f"  Suggestion: {c['suggestion']}")
        lines.append("")

    if analysis.get("outdated"):
        lines.append("## 🕐 Outdated Info")
        for o in analysis["outdated"]:
            lines.append(f"- **[{o['page']}]** `{o['field']}`: {o.get('current', '')} → {o.get('suggested', '')}")
            lines.append(f"  Reason: {o.get('reason', '')}")
        lines.append("")

    if analysis.get("relation_issues"):
        lines.append("## 🔗 Relation Issues")
        for r in analysis["relation_issues"]:
            lines.append(f"- **[{r['page']}]** {r['issue']}")
            lines.append(f"  Suggestion: {r['suggestion']}")
        lines.append("")

    if analysis.get("gaps"):
        lines.append("## 📋 Knowledge Gaps")
        for g in analysis["gaps"]:
            lines.append(f"- [{g.get('priority', '?').upper()}] **{g['topic']}**: {g['description']}")
        lines.append("")

    if patches:
        lines.append(f"## 🔧 Patches ({'Applied' if applied else 'Generated'})")
        for p in patches:
            lines.append(f"- [{p['page']}] {p['type']}: {p.get('field', p.get('issue', ''))}")
        lines.append("")

    summary_file.write_text("\n".join(lines), encoding='utf-8')
    print(f"📝 Summary saved: {summary_file}")


# ============ Main ============

def main():
    global LLM_API_KEY
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    apply_patches = "--apply" in args

    if not LLM_API_KEY:
        # Try loading from .env
        env_file = Path.home() / ".hermes" / ".env"
        if env_file.exists():
            for line in env_file.read_text().split('\n'):
                if line.startswith("GLM_API_KEY="):
                    LLM_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not LLM_API_KEY:
        print("❌ GLM_API_KEY not set. Set it via env var or ~/.hermes/.env")
        sys.exit(1)

    print(f"🌙 Dream Cycle — Wiki Knowledge Graph Auditor")
    print(f"   Model: {LLM_MODEL}")
    print(f"   Mode: {'DRY RUN' if dry_run else 'APPLY' if apply_patches else 'ANALYZE'}")
    print()

    # 1. Load pages
    print("📖 Loading wiki pages...")
    pages = load_all_pages()
    print(f"   Found {len(pages)} pages")
    if not pages:
        print("❌ No wiki pages found!")
        sys.exit(1)

    # 2. Build summary
    print("🔗 Building knowledge graph summary...")
    summary = build_knowledge_summary(pages)

    # 3. LLM Analysis
    analysis = analyze_knowledge_graph(summary)
    score = analysis.get("quality_score", {})
    print(f"\n📊 Quality Score: {score.get('overall', '?')}/10 "
          f"(coverage {score.get('coverage', '?')}, "
          f"consistency {score.get('consistency', '?')}, "
          f"freshness {score.get('freshness', '?')})")
    print(f"📝 Summary: {analysis.get('summary', 'N/A')}")

    # 4. Generate patches
    patches = generate_patches(analysis, pages)
    print(f"\n🔧 Generated {len(patches)} patches")

    if patches:
        for p in patches:
            print(f"   - [{p['page']}] {p['type']}: {p.get('field', p.get('issue', ''))}")

    # 5. Apply if requested
    applied = False
    if apply_patches and patches and not dry_run:
        print(f"\n🚀 Applying patches...")
        applied_count = 0
        for patch in patches:
            if apply_patch(patch):
                applied_count += 1
        print(f"   Applied {applied_count}/{len(patches)} patches")
        applied = True
    elif dry_run:
        print(f"\n🔍 Dry run — no changes made")

    # 5.5 Check Entity Registry for duplicates
    if HAS_REGISTRY:
        print(f"\n🔍 Checking Entity Registry for duplicates...")
        try:
            dupes = er.find_duplicates()
            if dupes:
                print(f"   ⚠ Found {len(dupes)} potential duplicate pairs:")
                for id1, id2, score in dupes[:10]:
                    reg = er.load_registry()
                    e1 = reg["entities"].get(id1, {})
                    e2 = reg["entities"].get(id2, {})
                    print(f"     - [{e1.get('canonical_name', id1)}] ↔ [{e2.get('canonical_name', id2)}] (score: {score:.2f})")
                    # Add to analysis as gap
                    analysis.setdefault("gaps", []).append({
                        "topic": f"Duplicate entity: {e1.get('canonical_name', id1)} / {e2.get('canonical_name', id2)}",
                        "description": f"Potential duplicate entities detected. Consider merging: {id1} + {id2}",
                        "priority": "high"
                    })
            else:
                print(f"   ✓ No duplicates found")
        except Exception as e:
            print(f"   ⚠ Registry check failed: {e}")

    # 6. Save report
    save_report(analysis, patches, applied)

    print(f"\n✨ Dream Cycle complete!")


if __name__ == "__main__":
    main()
