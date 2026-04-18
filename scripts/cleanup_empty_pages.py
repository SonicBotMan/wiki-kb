#!/usr/bin/env python3
"""
清理 Wiki Brain 空/低质量页面的脚本。
扫描所有 wiki 页面，找出 Key Facts 为空且 Executive Summary 过短的页面。

Usage:
    python cleanup_empty_pages.py [--dry-run] [--apply]
    --dry-run (默认): 只输出清理建议列表
    --apply: 将 low_value 页面的 status 改为 archived
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# 尝试导入 wiki_utils（与 wiki_mcp_server 共用工具函数）
try:
    from wiki_utils import get_frontmatter, update_frontmatter
except ImportError:
    # 如果无法导入，定义基础函数
    def get_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
        match = re.match(r'^---\n(.*?)\n---\n(.*)$', content, re.DOTALL)
        if match:
            fm_text = match.group(1)
            fm = {}
            for line in fm_text.split('\n'):
                m = re.match(r'^(\w+):\s*(.*)$', line)
                if m:
                    key, val = m.group(1), m.group(2)
                    val = val.strip().strip("'\"")
                    fm[key] = val
            return fm, match.group(2)
        return {}, content

    def update_frontmatter(content: str, updates: Dict[str, str]) -> str:
        fm, body = get_frontmatter(content)
        fm.update(updates)
        fm_lines = [f"{k}: {v}" for k, v in fm.items()]
        return "---\n" + "\n".join(fm_lines) + "\n---\n" + body

# 配置
WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", "/data"))
EXCLUDE_DIRS = {"logs", "scripts", "queries", "comparisons", "raw", "src"}
SUBDIRS = ["entities", "concepts", "projects", "persons", "meetings", "ideas", "comparisons", "queries", "tools"]

# 阈值
KEY_FACTS_EMPTY_MARKERS = ["_待补充_", "-", "N/A", ""]
SUMMARY_MIN_LENGTH = 30


def get_section_content(body: str, section: str) -> str:
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


def is_key_facts_empty(key_facts: str) -> bool:
    """检查 Key Facts 是否为空"""
    if not key_facts:
        return True
    cleaned = key_facts.strip()
    # 检查是否为"待补充"标记
    if cleaned in KEY_FACTS_EMPTY_MARKERS:
        return True
    # 检查是否只有单个破折号
    if cleaned == "-":
        return True
    return False


def is_summary_too_short(summary: str) -> bool:
    """检查 Executive Summary 是否过短"""
    if not summary:
        return True
    return len(summary.strip()) < SUMMARY_MIN_LENGTH


def scan_wiki_pages() -> List[Dict]:
    """扫描所有 wiki 页面，返回低价值页面列表"""
    low_value_pages = []

    for subdir in SUBDIRS:
        dir_path = WIKI_ROOT / subdir
        if not dir_path.exists():
            continue

        for md_file in dir_path.glob("*.md"):
            try:
                content = md_file.read_text(encoding='utf-8')
                fm, body = get_frontmatter(content)

                # 提取 Key Facts 和 Executive Summary
                key_facts = get_section_content(body, "Key Facts")
                summary = get_section_content(body, "Executive Summary")

                # 检查是否为空/短
                kf_empty = is_key_facts_empty(key_facts)
                sum_short = is_summary_too_short(summary)

                if kf_empty and sum_short:
                    low_value_pages.append({
                        "path": str(md_file.relative_to(WIKI_ROOT)),
                        "title": fm.get("title", md_file.stem),
                        "type": fm.get("type", "unknown"),
                        "status": fm.get("status", "unknown"),
                        "source": fm.get("source", ""),
                        "key_facts": key_facts[:50] + "..." if len(key_facts) > 50 else key_facts,
                        "summary": summary[:50] + "..." if len(summary) > 50 else summary,
                        "summary_len": len(summary.strip()),
                    })
            except Exception as e:
                print(f"  [WARN] 读取失败 {md_file}: {e}", file=sys.stderr)

    return low_value_pages


def print_report(pages: List[Dict], dry_run: bool = True):
    """打印清理报告"""
    mode = "[DRY-RUN]" if dry_run else "[APPLY]"
    print(f"\n{'='*70}")
    print(f"Wiki Brain 低质量页面清理报告 {mode}")
    print(f"{'='*70}")
    print(f"\n共发现 {len(pages)} 个低价值页面:\n")

    for i, page in enumerate(pages, 1):
        print(f"{i}. {page['path']}")
        print(f"   标题: {page['title']}")
        print(f"   类型: {page['type']} | 状态: {page['status']} | 来源: {page['source']}")
        print(f"   Key Facts: [{page['key_facts'][:40]}...]" if len(page['key_facts']) > 40 else f"   Key Facts: [{page['key_facts']}]")
        print(f"   Summary ({page['summary_len']} chars): [{page['summary'][:40]}...]" if len(page['summary']) > 40 else f"   Summary ({page['summary_len']} chars): [{page['summary']}]")
        print()

    print(f"{'='*70}")
    if dry_run:
        print("提示: 这是 dry-run 模式，未进行任何修改。")
        print("      使用 --apply 参数来将 status 改为 archived。")
    else:
        print(f"已将 {len(pages)} 个页面的 status 改为 archived。")
    print(f"{'='*70}\n")


def apply_archive(pages: List[Dict]):
    """将页面的 status 改为 archived"""
    archived_count = 0
    error_count = 0

    for page_info in pages:
        page_path = WIKI_ROOT / page_info["path"]
        try:
            content = page_path.read_text(encoding='utf-8')
            updated = update_frontmatter(content, {"status": "archived"})
            page_path.write_text(updated, encoding='utf-8')
            archived_count += 1
            print(f"  [OK] archived: {page_info['path']}")
        except Exception as e:
            error_count += 1
            print(f"  [ERROR] 失败 {page_info['path']}: {e}", file=sys.stderr)

    print(f"\n完成: {archived_count} 个页面已归档，{error_count} 个失败。")
    return archived_count, error_count


def main():
    parser = argparse.ArgumentParser(
        description="清理 Wiki Brain 空/低质量页面",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                      # dry-run 模式（默认）
  %(prog)s --dry-run            # dry-run 模式
  %(prog)s --apply              # 执行归档操作
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="只输出列表，不修改任何内容（默认）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="将低价值页面的 status 改为 archived"
    )

    args = parser.parse_args()

    # 检查 WIKI_ROOT 是否存在
    if not WIKI_ROOT.exists():
        print(f"错误: WIKI_ROOT 不存在: {WIKI_ROOT}", file=sys.stderr)
        print("请设置 WIKI_ROOT 环境变量指向 wiki 目录。", file=sys.stderr)
        sys.exit(1)

    print(f"扫描目录: {WIKI_ROOT}", file=sys.stderr)
    print(f"模式: {'APPLY' if args.apply else 'DRY-RUN'}\n", file=sys.stderr)

    # 扫描页面
    low_value_pages = scan_wiki_pages()

    # 输出报告
    print_report(low_value_pages, dry_run=not args.apply)

    # 如果是 apply 模式，执行归档
    if args.apply and low_value_pages:
        print("\n开始归档...\n")
        apply_archive(low_value_pages)


if __name__ == "__main__":
    main()
