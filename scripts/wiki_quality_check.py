#!/usr/bin/env python3
"""Wiki Brain Quality Checker — 验收脚本

检查 wiki 页面质量：
1. Key Facts 非空率（目标 ≥80%）
2. Executive Summary 质量（非空、非模板占位符）
3. 新建页面 vs 存量页面统计

用法：
  python3 wiki_quality_check.py [WIKI_ROOT]
  
  WIKI_ROOT 默认: /data/wiki  (Docker 容器内路径)
  本地测试: python3 wiki_quality_check.py /tmp/test-wiki
"""

import sys
import re
from pathlib import Path
from datetime import datetime


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from markdown."""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end].strip()
    fm = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip().strip('"').strip("'")
    return fm


def check_key_facts(body: str) -> dict:
    """Check Key Facts section quality."""
    result = {"has_section": False, "is_empty": True, "fact_count": 0, "facts": []}
    
    # Find Key Facts section
    match = re.search(r"## Key Facts\s*\n(.*?)(?=\n## |\n---|\Z)", body, re.DOTALL)
    if not match:
        return result
    
    result["has_section"] = True
    section = match.group(1).strip()
    
    # Check if it's placeholder
    if section == "_待补充_" or section == "_待补充" or not section:
        result["is_empty"] = True
        return result
    
    result["is_empty"] = False
    # Count bullet points
    facts = re.findall(r"^- (.+)$", section, re.MULTILINE)
    result["fact_count"] = len(facts)
    result["facts"] = facts
    
    return result


def check_executive_summary(body: str) -> dict:
    """Check Executive Summary section quality."""
    result = {"has_section": False, "length": 0, "quality": "empty"}
    
    match = re.search(r"## Executive Summary\s*\n(.*?)(?=\n## |\n---|\Z)", body, re.DOTALL)
    if not match:
        return result
    
    result["has_section"] = True
    text = match.group(1).strip()
    result["length"] = len(text)
    
    if not text or text == "_待补充_":
        result["quality"] = "empty"
    elif len(text) < 30:
        result["quality"] = "too_short"
    elif len(text) < 100:
        result["quality"] = "brief"
    else:
        result["quality"] = "substantive"
    
    return result


def scan_wiki(wiki_root: Path) -> list:
    """Scan all wiki pages and return quality metrics."""
    results = []
    
    # Scan all type directories
    for subdir in ["entities", "concepts", "projects", "persons", "organizations", "tools"]:
        dir_path = wiki_root / subdir
        if not dir_path.exists():
            continue
        
        for md_file in dir_path.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            
            # Find body (after frontmatter)
            body = content
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    body = content[end + 3:].strip()
            
            kf = check_key_facts(body)
            es = check_executive_summary(body)
            
            created = fm.get("created", "unknown")
            status = fm.get("status", "unknown")
            
            results.append({
                "file": str(md_file.relative_to(wiki_root)),
                "title": fm.get("title", md_file.stem),
                "type": subdir,
                "created": created,
                "status": status,
                "key_facts": kf,
                "exec_summary": es,
            })
    
    return results


def print_report(results: list):
    """Print quality report."""
    total = len(results)
    if total == 0:
        print("❌ No wiki pages found.")
        return
    
    # Key Facts stats
    kf_has_section = sum(1 for r in results if r["key_facts"]["has_section"])
    kf_non_empty = sum(1 for r in results if r["key_facts"]["has_section"] and not r["key_facts"]["is_empty"])
    kf_avg_count = sum(r["key_facts"]["fact_count"] for r in results) / total
    kf_rate = kf_non_empty / total * 100 if total > 0 else 0
    
    # Executive Summary stats
    es_substantive = sum(1 for r in results if r["exec_summary"]["quality"] == "substantive")
    es_brief = sum(1 for r in results if r["exec_summary"]["quality"] == "brief")
    es_short = sum(1 for r in results if r["exec_summary"]["quality"] == "too_short")
    es_empty = sum(1 for r in results if r["exec_summary"]["quality"] == "empty")
    es_avg_len = sum(r["exec_summary"]["length"] for r in results) / total
    
    # Status distribution
    statuses = {}
    for r in results:
        s = r["status"]
        statuses[s] = statuses.get(s, 0) + 1
    
    print("=" * 60)
    print(f"  Wiki Brain Quality Report")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print(f"\n📊 Overview: {total} pages")
    print(f"   Status: {', '.join(f'{k}={v}' for k, v in sorted(statuses.items()))}")
    
    print(f"\n📋 Key Facts")
    print(f"   Non-empty rate: {kf_rate:.1f}% ({kf_non_empty}/{total}) {'✅' if kf_rate >= 80 else '❌ <80%'}")
    print(f"   Avg facts/page: {kf_avg_count:.1f}")
    print(f"   Pages with section: {kf_has_section}")
    print(f"   Pages with placeholder (_待补充_): {kf_has_section - kf_non_empty}")
    
    print(f"\n📝 Executive Summary")
    print(f"   Substantive (≥100 chars): {es_substantive}")
    print(f"   Brief (30-100 chars):     {es_brief}")
    print(f"   Too short (<30 chars):    {es_short}")
    print(f"   Empty/placeholder:        {es_empty}")
    print(f"   Avg length: {es_avg_len:.0f} chars")
    
    # Show worst pages
    empty_kf = [r for r in results if r["key_facts"]["has_section"] and r["key_facts"]["is_empty"]]
    no_kf = [r for r in results if not r["key_facts"]["has_section"]]
    bad_pages = empty_kf + no_kf
    
    if bad_pages:
        print(f"\n⚠️  Pages with empty Key Facts ({len(bad_pages)}):")
        for r in bad_pages[:15]:
            print(f"   - [{r['type']}] {r['title']} ({r['file']})")
        if len(bad_pages) > 15:
            print(f"   ... and {len(bad_pages) - 15} more")
    
    # Show best pages (most facts)
    best = sorted(results, key=lambda r: r["key_facts"]["fact_count"], reverse=True)[:5]
    if best[0]["key_facts"]["fact_count"] > 0:
        print(f"\n✅ Top pages by Key Facts count:")
        for r in best:
            kf = r["key_facts"]
            print(f"   - [{r['type']}] {r['title']}: {kf['fact_count']} facts")
            for f in kf["facts"][:3]:
                print(f"       • {f[:80]}")
    
    print(f"\n{'=' * 60}")
    print(f"  TARGET: Key Facts non-empty rate ≥ 80%")
    print(f"  CURRENT: {kf_rate:.1f}%  {'✅ PASS' if kf_rate >= 80 else '❌ FAIL'}")
    print(f"{'=' * 60}")
    
    return kf_rate >= 80


def main():
    wiki_root = sys.argv[1] if len(sys.argv) > 1 else "/data/wiki"
    root = Path(wiki_root)
    
    if not root.exists():
        print(f"❌ Wiki root not found: {root}")
        sys.exit(1)
    
    results = scan_wiki(root)
    passed = print_report(results)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
