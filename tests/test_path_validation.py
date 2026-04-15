"""Tests for path traversal protection (P0-2)."""
import pytest
from pathlib import Path


class TestValidatePath:
    def test_blocks_absolute_path_outside_root(self, mcp_module):
        mcp_mod, root = mcp_module
        with pytest.raises(ValueError, match="traversal|bounds|outside"):
            mcp_mod._validate_path(Path("/etc/passwd"))

    def test_blocks_dotdot_traversal(self, mcp_module):
        mcp_mod, root = mcp_module
        evil = root / "concepts" / ".." / ".." / "etc" / "passwd"
        with pytest.raises(ValueError):
            mcp_mod._validate_path(evil.resolve())

    def test_allows_valid_page(self, mcp_module):
        mcp_mod, root = mcp_module
        page = root / "concepts" / "test-page.md"
        result = mcp_mod._validate_path(page)
        assert result == page

    def test_allows_nonexistent_within_root(self, mcp_module):
        """_validate_path checks bounds only, not existence."""
        mcp_mod, root = mcp_module
        ghost = root / "concepts" / "nonexistent.md"
        result = mcp_mod._validate_path(ghost)
        assert result == ghost

    def test_allows_non_md_within_root(self, mcp_module):
        """_validate_path checks bounds only, not extension."""
        mcp_mod, root = mcp_module
        txt = root / "concepts" / "readme.txt"
        txt.write_text("hello", encoding="utf-8")
        result = mcp_mod._validate_path(txt)
        assert result == txt

    def test_allows_src_dir_within_root(self, mcp_module):
        """_validate_path checks bounds only, not excluded dirs.
        Excluded dir filtering is handled by _resolve_page_path."""
        mcp_mod, root = mcp_module
        excluded = root / "src" / "images" / "evil.md"
        excluded.parent.mkdir(parents=True)
        excluded.write_text("---\ntitle: evil\n---\n", encoding="utf-8")
        result = mcp_mod._validate_path(excluded)
        assert result == excluded


class TestResolvePagePath:
    def test_exact_match(self, mcp_module):
        mcp_mod, root = mcp_module
        result = mcp_mod._resolve_page_path("test-page")
        assert result is not None
        assert result.name == "test-page.md"

    def test_fuzzy_match_validated(self, mcp_module):
        mcp_mod, root = mcp_module
        result = mcp_mod._resolve_page_path("test_page")
        assert result is not None

    def test_nonexistent_returns_none(self, mcp_module):
        mcp_mod, root = mcp_module
        assert mcp_mod._resolve_page_path("no-such-page") is None
