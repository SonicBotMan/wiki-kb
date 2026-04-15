"""Tests for unified frontmatter parsing (P1-1)."""
import datetime


class TestGetFrontmatter:
    def test_basic_frontmatter(self):
        from wiki_utils import get_frontmatter
        content = "---\ntitle: Hello\ntype: concept\n---\n\nbody"
        fm, body = get_frontmatter(content)
        assert fm["title"] == "Hello"
        assert fm["type"] == "concept"
        assert body.strip() == "body"

    def test_no_frontmatter(self):
        from wiki_utils import get_frontmatter
        content = "just body text"
        fm, body = get_frontmatter(content)
        assert fm == {}
        assert body == "just body text"

    def test_yaml_list_values(self):
        from wiki_utils import get_frontmatter
        content = "---\ntags:\n  - ai\n  - wiki\n---\nbody"
        fm, body = get_frontmatter(content)
        assert fm["tags"] == ["ai", "wiki"]

    def test_date_as_string(self):
        from wiki_utils import get_frontmatter
        content = "---\nupdated: 2026-04-15\n---\nbody"
        fm, body = get_frontmatter(content)
        assert isinstance(fm.get("updated"), str)
        assert fm["updated"] == "2026-04-15"

    def test_date_as_datetime_object(self):
        """yaml.safe_load parses '2026-04-15' as datetime.date — must normalize."""
        from wiki_utils import get_frontmatter
        content = "---\nupdated: 2026-04-15\ncreated: 2026-01-01\n---\nbody"
        fm, body = get_frontmatter(content)
        assert isinstance(fm["updated"], str)
        assert isinstance(fm["created"], str)
        assert fm["updated"] == "2026-04-15"

    def test_multiline_description(self):
        from wiki_utils import get_frontmatter
        content = "---\ntitle: Test\ndescription: |\n  Line 1\n  Line 2\n---\nbody"
        fm, body = get_frontmatter(content)
        assert "Line 1" in fm["description"]

    def test_empty_yaml_returns_empty_dict(self):
        """Empty YAML between delimiters returns empty dict."""
        from wiki_utils import get_frontmatter
        # yaml.safe_load("") returns None, which we normalize to {}
        content = "---\n\n---\nbody"
        fm, body = get_frontmatter(content)
        assert fm == {}
        assert "body" in body
