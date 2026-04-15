"""Tests for wiki_update security (P0-3: Timeline protection, section whitelist)."""
import pytest


class TestTimelineProtection:
    def test_reject_timeline(self, mcp_module):
        mcp_mod, root = mcp_module
        with pytest.raises(ValueError, match="append-only|Timeline"):
            mcp_mod.wiki_update("test-page", "timeline", "bad")

    def test_reject_timeline_case_insensitive(self, mcp_module):
        mcp_mod, root = mcp_module
        with pytest.raises(ValueError, match="append-only"):
            mcp_mod.wiki_update("test-page", "Timeline", "bad")

    def test_reject_timeline_with_spaces(self, mcp_module):
        mcp_mod, root = mcp_module
        with pytest.raises(ValueError, match="append-only"):
            mcp_mod.wiki_update("test-page", " timeline ", "bad")

    def test_reject_invalid_section(self, mcp_module):
        mcp_mod, root = mcp_module
        with pytest.raises(ValueError, match="Allowed"):
            mcp_mod.wiki_update("test-page", "random-section", "bad")


class TestValidSectionUpdate:
    def test_update_executive_summary(self, mcp_module):
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_update("test-page", "executive summary", "New summary")
        assert "Executive Summary" in result["updated_sections"]
        page = root / "concepts" / "test-page.md"
        content = page.read_text(encoding="utf-8")
        assert "New summary" in content

    def test_update_key_facts(self, mcp_module):
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_update("test-page", "Key Facts", "- Fact A\n- Fact B")
        assert "Key Facts" in result["updated_sections"]
