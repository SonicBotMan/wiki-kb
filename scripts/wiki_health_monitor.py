#!/usr/bin/env python3
"""
Wiki Health Monitor - Runs inside wiki-brain Docker container
Monitors wiki health and writes dashboard to /data/system/wiki-health.md
"""

import os
import re
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from collections import defaultdict


# Configuration
DATA_DIR = Path("/data")
CRON_LOG_PATH = DATA_DIR / "logs" / "cron.log"
DASHBOARD_PATH = DATA_DIR / "system" / "wiki-health.md"
STALE_THRESHOLD_DAYS = 30
WEEK_OLD_THRESHOLD = 7  # days

# Exclude patterns
EXCLUDE_DIRS = {"scripts", "logs", ".git", "src", "templates", "archived-books"}
EXCLUDE_FILES = {"dream-*.md"}

# Frontmatter regex
FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})$')

# Content patterns
KEY_FACTS_PATTERN = re.compile(r'^##\s+Key Facts', re.MULTILINE | re.IGNORECASE)
EXECUTIVE_SUMMARY_PATTERN = re.compile(r'^##\s+Executive Summary', re.MULTILINE | re.IGNORECASE)

# YAML field patterns (simple parsing, no external deps)
YAML_LINE_RE = re.compile(r'^(\w+):\s*(.*)$')


def parse_frontmatter(content: str) -> Optional[dict]:
    """Parse simple YAML frontmatter from markdown content (no external deps)."""
    match = FRONTMATTER_RE.match(content)
    if not match:
        return None
    
    fm = {}
    for line in match.group(1).splitlines():
        m = YAML_LINE_RE.match(line.strip())
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            
            # Handle arrays (simplified - just detect [...] presence)
            if value.startswith('[') and value.endswith(']'):
                # Extract comma-separated values
                inner = value[1:-1]
                fm[key] = [v.strip().strip('"').strip("'") for v in inner.split(',') if v.strip()]
            elif value.startswith('"') and value.endswith('"'):
                fm[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                fm[key] = value[1:-1]
            else:
                fm[key] = value
    
    return fm


def is_excluded(path: Path) -> bool:
    """Check if file/directory should be excluded."""
    parts = path.parts
    for exclude in EXCLUDE_DIRS:
        if exclude in parts:
            return True
    for pattern in EXCLUDE_FILES:
        if path.name == pattern or path.name.startswith("dream-"):
            return True
    return False


def collect_page_metrics() -> dict:
    """Collect all metrics from wiki pages."""
    metrics = {
        "total": 0,
        "by_type": defaultdict(lambda: {"total": 0, "active": 0, "draft": 0, "unknown": 0}),
        "by_status": defaultdict(int),
        "with_key_facts": 0,
        "with_executive_summary": 0,
        "stale": 0,
        "draft": 0,
        "new_this_week": 0,
        "recent_changes": [],
    }
    
    today = datetime.now().date()
    week_ago = today - timedelta(days=WEEK_OLD_THRESHOLD)
    stale_date = today - timedelta(days=STALE_THRESHOLD_DAYS)
    
    if not DATA_DIR.exists():
        return metrics
    
    for md_file in DATA_DIR.rglob("*.md"):
        if is_excluded(md_file):
            continue
        
        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        
        fm = parse_frontmatter(content)
        if not fm:
            continue
        
        metrics["total"] += 1
        
        # Type
        page_type = fm.get("type", "unknown")
        if page_type not in ("entity", "concept", "tool", "project", "meeting", "idea", "guide", "comparison", "query", "system"):
            page_type = "unknown"
        metrics["by_type"][page_type]["total"] += 1
        
        # Status
        status = fm.get("status", "unknown")
        if status not in ("active", "draft", "unknown"):
            status = "unknown"
        metrics["by_status"][status] += 1
        metrics["by_type"][page_type][status] += 1
        
        if status == "draft":
            metrics["draft"] += 1
        
        # Key Facts
        if KEY_FACTS_PATTERN.search(content):
            metrics["with_key_facts"] += 1
        
        # Executive Summary
        if EXECUTIVE_SUMMARY_PATTERN.search(content):
            metrics["with_executive_summary"] += 1
        
        # Dates
        created_str = fm.get("created", "")
        updated_str = fm.get("updated", "")
        
        created_date = None
        updated_date = None
        
        if DATE_RE.match(str(created_str)):
            try:
                created_date = datetime.strptime(created_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        
        if DATE_RE.match(str(updated_str)):
            try:
                updated_date = datetime.strptime(updated_str, "%Y-%m-%d").date()
            except ValueError:
                pass
        
        # Stale check (by updated date)
        if updated_date and updated_date < stale_date:
            metrics["stale"] += 1
        
        # New this week (by created date)
        if created_date and created_date >= week_ago:
            metrics["new_this_week"] += 1
        
        # Track recent changes for sorting
        if updated_date:
            metrics["recent_changes"].append({
                "title": fm.get("title", md_file.stem),
                "type": page_type,
                "updated": updated_str,
                "updated_date": updated_date,
            })
    
    # Sort recent changes by updated date descending, take top 5
    metrics["recent_changes"] = sorted(
        metrics["recent_changes"],
        key=lambda x: x["updated_date"],
        reverse=True
    )[:5]
    
    return metrics


def check_cron_health() -> dict:
    """Check cron pipeline health from cron.log."""
    health = {
        "dream_cycle": {"status": "✅", "error": "OK"},
        "memory_to_wiki": {"status": "✅", "error": "OK"},
        "auto_index": {"status": "✅", "error": "OK"},
        "total_errors": 0,
    }
    
    if not CRON_LOG_PATH.exists():
        return health
    
    try:
        lines = CRON_LOG_PATH.read_text(encoding="utf-8").splitlines()
        last_lines = lines[-50:] if len(lines) > 50 else lines
    except (OSError, UnicodeDecodeError):
        return health
    
    # Check dream_cycle: RuntimeError or HTTP Error 401
    dream_errors = []
    for line in last_lines:
        if "dream_cycle" in line.lower():
            if "RuntimeError" in line or "HTTP Error 401" in line:
                dream_errors.append(line.strip())
    if dream_errors:
        health["dream_cycle"] = {"status": "❌", "error": dream_errors[-1][:60]}
    
    # Check memory_to_wiki: No memories or ERROR
    memory_errors = []
    for line in last_lines:
        if "memory_to_wiki" in line.lower():
            if "No memories" in line or "ERROR" in line:
                memory_errors.append(line.strip())
    if memory_errors:
        health["memory_to_wiki"] = {"status": "❌", "error": memory_errors[-1][:60]}
    
    # Check auto_index: Sync complete
    auto_synced = False
    for line in last_lines:
        if "auto_index" in line.lower():
            if "Sync complete" in line:
                auto_synced = True
    if not auto_synced:
        health["auto_index"] = {"status": "⚠️", "error": "No recent sync"}
    
    # Count all errors in last 50 lines
    error_count = 0
    for line in last_lines:
        if "ERROR" in line or "RuntimeError" in line or "HTTP Error 401" in line:
            error_count += 1
    health["total_errors"] = error_count
    
    return health


def calculate_status(metrics: dict, cron_health: dict) -> tuple[str, list]:
    """Calculate overall health status and alerts."""
    alerts = []
    is_critical = False
    is_warning = False
    
    total = metrics["total"]
    if total == 0:
        return "✅", ["No pages found"]
    
    draft = metrics["draft"]
    stale = metrics["stale"]
    stale_pct = (stale / total * 100) if total > 0 else 0
    key_facts_pct = (metrics["with_key_facts"] / total * 100) if total > 0 else 0
    
    # Check critical conditions
    for pipeline, info in [("dream_cycle", cron_health["dream_cycle"]),
                           ("memory_to_wiki", cron_health["memory_to_wiki"]),
                           ("auto_index", cron_health["auto_index"])]:
        if info["status"] == "❌":
            is_critical = True
            alerts.append(f"❌ {pipeline} has errors: {info['error']}")
    
    if stale_pct > 50:
        is_critical = True
        alerts.append(f"❌ {stale} pages are stale ({stale_pct:.0f}%)")
    
    # Check warning conditions
    if draft > 5:
        is_warning = True
        alerts.append(f"⚠️ {draft} pages are drafts (>5)")
    
    if stale_pct > 30 and not is_critical:
        is_warning = True
        alerts.append(f"⚠️ {stale} pages are stale ({stale_pct:.0f}%)")
    
    if key_facts_pct < 50:
        is_warning = True
        missing = total - metrics["with_key_facts"]
        alerts.append(f"⚠️ {missing} pages have no Key Facts section")
    
    if metrics["with_executive_summary"] == 0 and total > 5:
        is_warning = True
        alerts.append(f"⚠️ No pages have Executive Summary section")
    
    if is_critical:
        return "❌", alerts
    elif is_warning:
        return "⚠️", alerts
    return "✅", alerts


def generate_dashboard(metrics: dict, cron_health: dict, alerts: list) -> str:
    """Generate the wiki health dashboard markdown."""
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    
    total = metrics["total"]
    active = metrics["by_status"].get("active", 0)
    draft = metrics["draft"]
    stale = metrics["stale"]
    
    active_pct = (active / total * 100) if total > 0 else 0
    stale_pct = (stale / total * 100) if total > 0 else 0
    key_facts_pct = (metrics["with_key_facts"] / total * 100) if total > 0 else 0
    summary_pct = (metrics["with_executive_summary"] / total * 100) if total > 0 else 0
    
    def status_for_threshold(value, warning_threshold, critical_threshold=None):
        if critical_threshold and value >= critical_threshold:
            return "❌"
        if value >= warning_threshold:
            return "⚠️"
        return "✅"
    
    overview_statuses = {
        "total": "✅" if total > 0 else "⚠️",
        "active": "✅" if active_pct >= 70 else "⚠️",
        "draft": "✅" if draft <= 5 else "⚠️",
        "stale": status_for_threshold(stale_pct, 30, 50),
        "key_facts": "✅" if key_facts_pct >= 50 else "⚠️",
        "summary": "✅" if summary_pct >= 20 or total <= 5 else "⚠️",
        "new": "✅",
    }
    
    lines = [
        "---",
        "title: Wiki Health Dashboard",
        "type: system",
        "status: active",
        f"created: {now_str.split()[0]}",
        f"updated: {now_str.split()[0]}",
        "---",
        "",
        "# Wiki Health Dashboard",
        "",
        f"> Auto-generated by wiki_health_monitor.py. Last run: {now_str}",
        "",
        "## Overview",
        "",
        "| Metric | Value | Status |",
        "|--------|-------|--------|",
        f"| Total Pages | {total} | {overview_statuses['total']} |",
        f"| Active | {active} ({active_pct:.0f}%) | {overview_statuses['active']} |",
        f"| Draft | {draft} | {overview_statuses['draft']} |",
        f"| Stale (>30d) | {stale} ({stale_pct:.0f}%) | {overview_statuses['stale']} |",
        f"| With Key Facts | {metrics['with_key_facts']} ({key_facts_pct:.0f}%) | {overview_statuses['key_facts']} |",
        f"| With Summary | {metrics['with_executive_summary']} ({summary_pct:.0f}%) | {overview_statuses['summary']} |",
        f"| New This Week | {metrics['new_this_week']} | {overview_statuses['new']} |",
        "",
        "## By Type",
        "",
        "| Type | Count | Active | Draft |",
        "|------|-------|--------|-------|",
    ]
    
    # Sort types for consistent output
    type_order = ["entity", "concept", "tool", "project", "meeting", "idea", "guide", "comparison", "query", "system", "unknown"]
    for ptype in sorted(metrics["by_type"].keys(), key=lambda x: (type_order.index(x) if x in type_order else 99, x)):
        type_data = metrics["by_type"][ptype]
        lines.append(f"| {ptype} | {type_data['total']} | {type_data['active']} | {type_data['draft']} |")
    
    lines.extend([
        "",
        "## Cron Pipeline Status",
        "",
        "| Pipeline | Status | Last Error |",
        "|----------|--------|------------|",
        f"| dream_cycle | {cron_health['dream_cycle']['status']} | {cron_health['dream_cycle']['error']} |",
        f"| memory_to_wiki | {cron_health['memory_to_wiki']['status']} | {cron_health['memory_to_wiki']['error']} |",
        f"| auto_index | {cron_health['auto_index']['status']} | {cron_health['auto_index']['error']} |",
        "",
        "## Alerts",
        "",
    ])
    
    if alerts:
        for alert in alerts:
            lines.append(f"- {alert}")
    else:
        lines.append("- No alerts - all systems healthy")
    
    lines.extend([
        "",
        "## Recent Changes",
        "",
        "Last 5 pages updated (by frontmatter updated date):",
        "",
    ])
    
    if metrics["recent_changes"]:
        for i, page in enumerate(metrics["recent_changes"], 1):
            lines.append(f"{i}. {page['title']} ({page['type']}, {page['updated']})")
    else:
        lines.append("- No recent changes")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Wiki Health Monitor")
    parser.add_argument("--notify", action="store_true", help="Print alert summary for Discord webhook")
    args = parser.parse_args()
    
    # Collect metrics
    metrics = collect_page_metrics()
    cron_health = check_cron_health()
    
    # Calculate status and generate alerts
    status, alerts = calculate_status(metrics, cron_health)
    
    # Generate dashboard with alerts
    dashboard = generate_dashboard(metrics, cron_health, alerts)
    
    # Write dashboard
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(dashboard, encoding="utf-8")
    
    total = metrics["total"]
    draft = metrics["draft"]
    stale = metrics["stale"]
    
    # Build one-line status
    if status == "✅":
        msg = f"HEALTH OK: {total} pages, {draft} drafts, {stale} stale, all pipelines green"
    else:
        alert_parts = []
        for alert in alerts[:3]:
            alert_parts.append(alert.replace("❌", "").replace("⚠️", "").strip())
        msg = f"HEALTH ALERT: {', '.join(alert_parts)}"
    
    print(msg)
    
    # If --notify and there are critical alerts, print extra line for Discord
    if args.notify and any("❌" in a for a in alerts):
        print(f"ACTION REQUIRED: {len([a for a in alerts if '❌' in a])} critical issue(s) detected", file=sys.stderr)


if __name__ == "__main__":
    main()
