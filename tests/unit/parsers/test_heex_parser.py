
import pytest
from codegraphcontext.utils.tree_sitter_manager import get_tree_sitter_manager
from codegraphcontext.tools.languages.heex import HeexTreeSitterParser
from unittest.mock import MagicMock


class TestHeexParser:
    """Test the HEEx Parser logic."""

    @pytest.fixture(scope="class")
    def parser(self):
        manager = get_tree_sitter_manager()
        wrapper = MagicMock()
        wrapper.language_name = "heex"
        wrapper.language = manager.get_language_safe("heex")
        wrapper.parser = manager.create_parser("heex")
        return HeexTreeSitterParser(wrapper)

    def test_parse_components(self, parser, temp_test_dir):
        """Parse HEEx components."""
        code = """
<.form for={@changeset} phx-submit="save">
  <.input field={@form[:name]} type="text" />
  <.button>Save</.button>
</.form>
"""
        f = temp_test_dir / "test.heex"
        f.write_text(code)

        result = parser.parse(str(f))

        assert "functions" in result
        components = result["functions"]
        comp_names = {c["name"] for c in components}
        assert ".form" in comp_names
        assert ".input" in comp_names
        assert ".button" in comp_names
        assert result["lang"] == "heex"

    def test_parse_directives(self, parser, temp_test_dir):
        """Parse EEx directives."""
        code = """
<h1><%= @title %></h1>
<p><%= @description %></p>
"""
        f = temp_test_dir / "directives.heex"
        f.write_text(code)

        result = parser.parse(str(f))

        variables = result["variables"]
        assert len(variables) >= 2

    def test_parse_module_component(self, parser, temp_test_dir):
        """Parse module-qualified component references as imports."""
        code = """
<MyAppWeb.Components.header title={@page_title} />
"""
        f = temp_test_dir / "module_comp.heex"
        f.write_text(code)

        result = parser.parse(str(f))

        imports = result["imports"]
        assert len(imports) >= 1
        assert imports[0]["name"] == "MyAppWeb.Components"

    def test_parse_html_tags(self, parser, temp_test_dir):
        """Parse basic HTML tags (they should still be processed)."""
        code = """
<div class="container">
  <h1>Title</h1>
  <p>Content</p>
</div>
"""
        f = temp_test_dir / "tags.heex"
        f.write_text(code)

        result = parser.parse(str(f))

        # HTML tags are parsed but not mapped to functions
        # The result should still have the correct language
        assert result["lang"] == "heex"
        assert "functions" in result
