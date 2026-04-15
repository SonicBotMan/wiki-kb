"""Tests for wiki_create YAML injection prevention (SEC-1)."""
import pytest


class TestWikiCreateInjection:
    """Verify that wiki_create sanitizes name/status to prevent YAML injection."""

    def test_newline_in_name_sanitized(self, mcp_module):
        """Newlines in name must not inject frontmatter fields."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="Test\ntags: [evil]",
            type="concept",
            description="desc"
        )
        data = result if isinstance(result, dict) else eval(result)
        page = root / data["page_path"]
        page_content = page.read_text(encoding="utf-8")
        fm, body = mcp_mod._get_frontmatter(page_content)
        # tags must remain empty (sanitized newline prevents field injection)
        assert fm.get("tags") == [] or fm.get("tags") == ""
        # status must be draft (not overridden by injection)
        assert fm.get("status") == "draft"
        # title should be sanitized (newline replaced with space)
        assert "\n" not in fm.get("title", "")

    def test_newline_in_status_sanitized(self, mcp_module):
        """Newlines in status must not inject extra fields."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="Safe Page Status",
            type="concept",
            description="desc",
            status="active\nevil: true"
        )
        data = result if isinstance(result, dict) else eval(result)
        page = root / data["page_path"]
        page_content = page.read_text(encoding="utf-8")
        fm, body = mcp_mod._get_frontmatter(page_content)
        assert "evil" not in fm

    def test_long_name_rejected(self, mcp_module):
        """Names over 200 characters must be rejected."""
        mcp_mod, root = mcp_module
        with pytest.raises(ValueError, match="too long"):
            mcp_mod.wiki_create(
                name="A" * 300,
                type="concept",
                description="desc"
            )

    def test_default_status_is_draft(self, mcp_module):
        """New pages must default to 'draft' status."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="Draft Status Test",
            type="concept",
            description="test page"
        )
        data = result if isinstance(result, dict) else eval(result)
        assert data.get("status") == "draft", f"Expected draft, got {data.get('status')}"

    def test_carriage_return_stripped(self, mcp_module):
        """Carriage returns must also be stripped from name."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="CR Test\rPage",
            type="concept",
            description="desc"
        )
        data = result if isinstance(result, dict) else eval(result)
        page = root / data["page_path"]
        page_content = page.read_text(encoding="utf-8")
        fm, body = mcp_mod._get_frontmatter(page_content)
        assert "\r" not in fm.get("title", "")
