#!/usr/bin/env python3
"""
Wiki Utilities - Shared functions for frontmatter parsing
Used by wiki_mcp_server.py, entity_registry.py, and other scripts.
"""

import re
from typing import Dict, Tuple


def parse_frontmatter(content: str) -> Dict[str, str]:
    """
    Parse Markdown frontmatter, returning a dict.
    
    Args:
        content: Full markdown file content
        
    Returns:
        Dict of frontmatter key-value pairs, empty dict if no frontmatter
    """
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
        return fm
    return {}


def get_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """
    Extract markdown file's frontmatter.
    
    Args:
        content: Full markdown file content
        
    Returns:
        Tuple of (frontmatter_dict, body_content)
    """
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


def get_frontmatter_field(content: str, field: str) -> str:
    """
    Get a single frontmatter field value.
    
    Args:
        content: Full markdown file content
        field: Field name to retrieve
        
    Returns:
        Field value or empty string if not found
    """
    fm = parse_frontmatter(content)
    return fm.get(field, "")


def format_frontmatter(data: Dict[str, str]) -> str:
    """
    Format a dict as YAML frontmatter string.
    
    Args:
        data: Dict of key-value pairs
        
    Returns:
        Formatted frontmatter string with --- delimiters
    """
    fm_lines = [f"{k}: {v}" for k, v in data.items()]
    return "---\n" + "\n".join(fm_lines) + "\n---"


def update_frontmatter(content: str, updates: Dict[str, str]) -> str:
    """
    Update frontmatter with specified fields.
    
    Args:
        content: Full markdown file content
        updates: Dict of fields to update/add
        
    Returns:
        Updated markdown content
    """
    fm, body = get_frontmatter(content)
    fm.update(updates)
    return format_frontmatter(fm) + "\n" + body


def parse_tags(tag_str: str) -> list:
    """
    Parse tags string to list.
    
    Args:
        tag_str: Tags string like "[tag1, tag2]" or "tag1, tag2"
        
    Returns:
        List of tag strings
    """
    if not tag_str:
        return []
    tag_str = tag_str.strip('[]')
    if not tag_str:
        return []
    tags = [t.strip() for t in tag_str.split(',')]
    return [t for t in tags if t]
