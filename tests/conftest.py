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
                   "meetings", "ideas", "comparisons", "queries", "tools",
                   "logs"):
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


def _make_mock_fastmcp():
    """Create a mock FastMCP that makes @mcp.tool() a no-op decorator."""
    mock_instance = MagicMock()
    mock_instance.tool.return_value = lambda fn: fn  # decorator returns original function
    mock_class = MagicMock(return_value=mock_instance)
    return mock_class


@pytest.fixture
def mcp_module(tmp_path):
    """Import wiki_mcp_server with mocked WIKI_ROOT and mocked mcp package.

    Returns (module, wiki_root_path) tuple.
    """
    root = _create_wiki_root(tmp_path)

    # Clear cached modules
    mods_to_remove = [m for m in sys.modules
                      if m.startswith(("wiki_mcp_server", "entity_registry", "mcp"))]
    for m in mods_to_remove:
        del sys.modules[m]

    # Build mock mcp package hierarchy
    mock_fastmcp_mod = MagicMock()
    mock_fastmcp_mod.FastMCP = _make_mock_fastmcp()

    scripts_dir = str(Path(__file__).parent.parent / "scripts")

    with patch.dict(os.environ, {"WIKI_ROOT": str(root)}),          patch.dict(sys.modules, {
             "mcp": MagicMock(),
             "mcp.server": MagicMock(),
             "mcp.server.fastmcp": mock_fastmcp_mod,
         }):
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        try:
            import wiki_mcp_server as mod
            # Override module-level variables to point to temp dir
            mod.WIKI_ROOT = root
            mod.REGISTRY_FILE = root / "registry.json"
            yield mod, root
        finally:
            if scripts_dir in sys.path:
                sys.path.remove(scripts_dir)
            mods_to_remove = [m for m in sys.modules if m.startswith("wiki_mcp_server")]
            for m in mods_to_remove:
                del sys.modules[m]
