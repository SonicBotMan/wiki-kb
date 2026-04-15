#!/usr/bin/env python3
"""
Wiki Brain 共享工具函数。
所有脚本共享的 markdown 解析、frontmatter 处理等工具。
"""

import re
import datetime
import yaml


def get_frontmatter(content: str) -> tuple:
    """提取 markdown 文件的 frontmatter (YAML) 和正文。

    Returns:
        (dict, str) — frontmatter 字典和正文内容。
        如果无 frontmatter，返回 ({}, content)。
    """
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
    if match:
        fm_text = match.group(1)
        body = match.group(2)
        try:
            fm = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError:
            fm = {}
        # Normalize datetime.date objects to YYYY-MM-DD strings
        for k, v in fm.items():
            if isinstance(v, datetime.date):
                fm[k] = v.strftime("%Y-%m-%d")
        if not isinstance(fm, dict):
            fm = {}
        return fm, body
    return {}, content


def parse_relations_table(body: str) -> list:
    """从 markdown 正文提取 Relations 表格行。

    Returns:
        [{"relation": ..., "target": ..., "note": ...}, ...]
    """
    relations = []
    in_relations = False
    header_seen = False
    for line in body.split('\n'):
        if line.strip().startswith('## Relations'):
            in_relations = True
            header_seen = False
            continue
        if in_relations and line.strip().startswith('## '):
            break
        if in_relations and line.strip().startswith('|'):
            # 跳过分隔行 (| --- | --- |)
            if '---' in line:
                continue
            parts = [p.strip() for p in line.split('|')]
            # 跳过表头行（通常第二列为 "目标" 或 "target"）
            if not header_seen:
                header_seen = True
                continue
            if len(parts) >= 4:
                relations.append({
                    "relation": parts[1],
                    "target": parts[2],
                    "note": parts[3] if len(parts) > 3 else ""
                })
    return relations
