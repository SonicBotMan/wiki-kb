"""Shared test fixtures for wiki-kb tests."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _create_wiki_root(tmp_path):
    """Create a temporary wiki root with standard directory structure."""
    root = tmp_path / "wiki"
    for subdir in ("concepts", "entities", "people", "projects",
                   "meetings", "ideas", "comparisons", "queries", "tools"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    # Create a sample page
    sample = root / "concepts" / "test-page.md"
    sample.write_text(
        "---\ntitle: Test Page\ntype: concept\nstatus: active\nupdated: 2026-04-15\n---\n"
        "\n## Executive Summary\n\nTest summary.\n"
        "\n## Key Facts\n\n- Fact 1\n"
        "\n## Relations\n\n| Relation | Target | Note |\n|---|---|---|\n| uses | hermes | |\n"
        "\n## Timeline\n\n- **2026-04-15** | Created\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def tmp_wiki_root(tmp_path):
    """Create a temporary wiki root with standard directory structure."""
    return _create_wiki_root(tmp_path)


@pytest.fixture
def mcp_module(tmp_path):
    """Import wiki_mcp_server with mocked WIKI_ROOT.
    
    Returns (module, wiki_root_path) tuple.
    """
    root = _create_wiki_root(tmp_path)
    
    # Remove cached module to allow fresh import
    if "wiki_mcp_server" in sys.modules:
        del sys.modules["wiki_mcp_server"]
    
    scripts_dir = str(Path(__file__).parent.parent)
    with patch.dict(os.environ, {"WIKI_ROOT": str(root)}):
        sys.path.insert(0, scripts_dir)
        try:
            import wiki_mcp_server as mcp_mod
            yield mcp_mod, root
        finally:
            sys.path.remove(scripts_dir)
            if "wiki_mcp_server" in sys.modules:
                del sys.modules["wiki_mcp_server"]
