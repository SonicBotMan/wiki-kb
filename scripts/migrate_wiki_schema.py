#!/usr/bin/env python3
"""
migrate_wiki_schema.py — 将 wiki 页面从 SCHEMA v2 迁移到 v3 (Compiled Truth + Timeline)。

迁移规则：
- compiled-truth blockquote → Executive Summary 段落
- TL;DR → 补充进 Executive Summary
- 概述/核心能力等内容 → Key Facts
- Timeline 表格 → Timeline 列表（append-only 格式）
- Relations 表 → 保留
- 添加 --- 分隔线（compiled truth 与 timeline 分层）
- 在 frontmatter 中添加 status: active

用法:
  python3 migrate_wiki_schema.py          # 预览（不修改文件）
  python3 migrate_wiki_schema.py --apply  # 执行迁移
"""

import re
import sys
from datetime import datetime
from pathlib import Path

WIKI_ROOT = Path.home() / "wiki"
DRY_RUN = "--apply" not in sys.argv


def parse_frontmatter(content: str) -> tuple[str, dict, str]:
    """Returns (before_frontmatter, fm_dict, after_frontmatter)"""
    match = re.match(r'^(---\s*\n)(.*?)(\n---)', content, re.DOTALL)
    if not match:
        return "", {}, content

    before = ""
    fm_text = match.group(2)
    after = content[match.end():]

    fm = {}
    for line in fm_text.split('\n'):
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith('[') and val.endswith(']'):
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(',')]
            fm[key] = val

    return before, fm, after


def extract_compiled_truth(body: str) -> str:
    """Extract compiled-truth from blockquote."""
    match = re.search(r'\*\*compiled-truth\*\*:\s*(.+?)(?:\n|$)', body)
    return match.group(1).strip() if match else ""


def extract_aliases(body: str) -> str:
    """Extract aliases from blockquote."""
    match = re.search(r'\*\*aliases\*\*:\s*(.+?)(?:\n|$)', body)
    return match.group(1).strip() if match else ""


def extract_status(body: str) -> str:
    """Extract status from blockquote."""
    match = re.search(r'\*\*status\*\*:\s*(\w+)', body)
    return match.group(1).strip() if match else "active"


def extract_tldr(body: str) -> str:
    """Extract TL;DR section content."""
    match = re.search(r'## TL;DR\s*\n(.*?)(?=\n## |\n---|\Z)', body, re.DOTALL)
    return match.group(1).strip() if match else ""


def extract_timeline_table(body: str) -> list[dict]:
    """Extract Timeline table entries."""
    entries = []
    in_timeline = False
    for line in body.split('\n'):
        if re.match(r'^## Timeline', line):
            in_timeline = True
            continue
        if in_timeline and re.match(r'^## ', line):
            break
        if in_timeline and line.strip().startswith('|'):
            # Skip table header and separator rows
            stripped = line.strip()
            if re.match(r'^\|[\s\-|]+\|$', stripped):  # |---|---|
                continue
            if re.match(r'^\|\s*(时间|日期|Time|Date)\s*\|', stripped):  # header row
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) >= 3 and parts[1]:
                date_str = parts[1].strip()
                event = parts[2].strip()
                if date_str and event:
                    entries.append({"date": date_str, "event": event})
    return entries


def extract_relations(body: str) -> str:
    """Extract Relations section as-is."""
    match = re.search(r'(## Relations\s*\n\|.+\n(?:\|.+\n)*)', body, re.DOTALL)
    return match.group(1).strip() if match else ""


def extract_body_sections(body: str) -> list[tuple[str, str]]:
    """Extract all ## sections (name, content) after the header block."""
    sections = []
    # Skip the header block (title + blockquotes)
    # Find where the first ## section starts (not TL;DR, not Timeline, not Relations)
    lines = body.split('\n')
    skip_sections = {'TL;DR', 'Timeline', 'Relations'}
    
    current_section = None
    current_lines = []
    
    for line in lines:
        h2_match = re.match(r'^## (.+)', line)
        if h2_match:
            section_name = h2_match.group(1).strip()
            if section_name in skip_sections:
                if current_section and current_lines:
                    sections.append((current_section, '\n'.join(current_lines).strip()))
                current_section = None
                current_lines = []
                continue
            if current_section and current_lines:
                sections.append((current_section, '\n'.join(current_lines).strip()))
            current_section = section_name
            current_lines = []
        elif current_section:
            current_lines.append(line)
    
    if current_section and current_lines:
        sections.append((current_section, '\n'.join(current_lines).strip()))
    
    return sections


def build_new_page(fm: dict, compiled_truth: str, tldr: str, 
                   timeline_entries: list, relations: str,
                   body_sections: list, aliases: str, status: str) -> str:
    """Build the new v3 page."""
    lines = []
    
    # Frontmatter
    lines.append('---')
    lines.append(f'title: {fm.get("title", "Untitled")}')
    lines.append(f'created: {fm.get("created", datetime.now().strftime("%Y-%m-%d"))}')
    lines.append(f'updated: {datetime.now().strftime("%Y-%m-%d")}')
    lines.append(f'type: {fm.get("type", "concept")}')
    tags = fm.get("tags", [])
    if isinstance(tags, list):
        lines.append(f'tags: [{", ".join(str(t) for t in tags)}]')
    else:
        lines.append(f'tags: {tags}')
    sources = fm.get("sources", [])
    if isinstance(sources, list):
        lines.append(f'sources: [{", ".join(str(s) for s in sources)}]')
    else:
        lines.append(f'sources: {sources}')
    lines.append(f'status: {status}')
    lines.append('---')
    lines.append('')
    
    # Title
    title = fm.get("title", "Untitled")
    lines.append(f'# {title}')
    lines.append('')
    
    # Executive Summary (compiled-truth + TL;DR combined)
    lines.append('## Executive Summary')
    lines.append('')
    if compiled_truth:
        lines.append(compiled_truth)
    if tldr and tldr != compiled_truth:
        lines.append('')
        lines.append(tldr)
    lines.append('')
    
    # Key Facts (from body sections)
    if body_sections:
        lines.append('## Key Facts')
        lines.append('')
        for section_name, section_content in body_sections:
            lines.append(f'### {section_name}')
            lines.append('')
            lines.append(section_content)
            lines.append('')
    
    # Relations
    if relations:
        lines.append(relations)
        lines.append('')
    
    # Separator (the critical divider)
    lines.append('---')
    lines.append('')
    
    # Timeline (append-only format)
    lines.append('## Timeline')
    lines.append('')
    if timeline_entries:
        for entry in timeline_entries:
            lines.append(f'- **{entry["date"]}** | {entry["event"]}')
        lines.append('')
    else:
        lines.append(f'- **{datetime.now().strftime("%Y-%m-%d")}** | Page created')
        lines.append(f'  [Source: wiki migration, {datetime.now().strftime("%Y-%m-%d")}]')
        lines.append('')
    
    return '\n'.join(lines)


def migrate_file(filepath: Path) -> bool:
    """Migrate a single wiki file from v2 to v3."""
    content = filepath.read_text(encoding='utf-8')
    _, fm, body = parse_frontmatter(content)
    
    if not fm:
        print(f"  ⚠ {filepath.name}: no frontmatter, skipping")
        return False
    
    # Extract components
    compiled_truth = extract_compiled_truth(body)
    aliases = extract_aliases(body)
    status = extract_status(body)
    tldr = extract_tldr(body)
    timeline_entries = extract_timeline_table(body)
    relations = extract_relations(body)
    body_sections = extract_body_sections(body)
    
    # Build new page
    new_content = build_new_page(
        fm, compiled_truth, tldr, timeline_entries,
        relations, body_sections, aliases, status
    )
    
    if DRY_RUN:
        print(f"  [PREVIEW] {filepath.name}")
        print(f"    compiled-truth: {compiled_truth[:80]}...")
        print(f"    timeline entries: {len(timeline_entries)}")
        print(f"    relations: {'yes' if relations else 'no'}")
        print(f"    body sections: {[s[0] for s in body_sections]}")
    else:
        filepath.write_text(new_content, encoding='utf-8')
        print(f"  ✓ {filepath.name}")
    
    return True


def main():
    print(f"Wiki Schema Migration: v2 → v3")
    print(f"Mode: {'DRY RUN (preview only)' if DRY_RUN else 'APPLY'}")
    print()
    
    migrated = 0
    for subdir in ['concepts', 'entities', 'people', 'projects', 'meetings', 'ideas', 'comparisons', 'queries', 'tools']:
        dir_path = WIKI_ROOT / subdir
        if not dir_path.exists():
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            print(f"Processing {subdir}/{md_file.name}...")
            if migrate_file(md_file):
                migrated += 1
    
    print(f"\n{'Would migrate' if DRY_RUN else 'Migrated'} {migrated} files.")
    if DRY_RUN:
        print("Run with --apply to execute migration.")


if __name__ == "__main__":
    main()
