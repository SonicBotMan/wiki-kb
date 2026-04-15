"""Tests for wiki_review tool (DOC-2)."""
import json
import re
import pytest


class TestWikiReview:
    """Verify wiki_review promotes draft pages and returns feedback."""

    def _parse(self, result):
        return result if isinstance(result, dict) else json.loads(result)

    def test_draft_page_promoted_on_pass(self, mcp_module):
        """A well-structured draft page should be promoted to active."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="Review Good Page",
            type="concept",
            description="A well-structured page about AI agents and automation."
        )
        data = self._parse(result)
        page_id = data["page_path"]

        # Add Key Facts (required by structural check)
        page_file = root / page_id
        content = page_file.read_text(encoding="utf-8")
        content = content.replace("## Key Facts\n\n-", "## Key Facts\n\n- Fact 1: AI agents automate tasks")
        page_file.write_text(content, encoding="utf-8")

        review_result = mcp_mod.wiki_review(page_id)
        review_data = self._parse(review_result)
        assert review_data.get("status") == "active", f"Expected active, got {review_data}"
        assert review_data.get("passed") is True

    def test_page_missing_sections_fails_review(self, mcp_module):
        """A page with Key Facts removed should fail review."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="Review Missing Section",
            type="concept",
            description="test content"
        )
        data = self._parse(result)
        page_id = data["page_path"]

        # Remove Key Facts section entirely
        page_file = root / page_id
        content = page_file.read_text(encoding="utf-8")
        content = re.sub("## Key Facts\n\n-\n", "", content)
        page_file.write_text(content, encoding="utf-8")

        review_result = mcp_mod.wiki_review(page_id)
        review_data = self._parse(review_result)
        assert review_data.get("passed") is False
        assert len(review_data.get("feedback", [])) > 0

    def test_already_active_skips_review(self, mcp_module):
        """Reviewing an already-active page should be a no-op."""
        mcp_mod, root = mcp_module
        result = mcp_mod.wiki_create(
            name="Already Active Page",
            type="concept",
            description="test content"
        )
        data = self._parse(result)
        page_id = data["page_path"]

        # Promote
        mcp_mod.wiki_review(page_id)

        # Review again should say no review needed
        review_result = mcp_mod.wiki_review(page_id)
        review_data = self._parse(review_result)
        assert "no review needed" in review_data.get("message", "").lower()
