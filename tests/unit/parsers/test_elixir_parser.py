
import pytest
from codegraphcontext.utils.tree_sitter_manager import get_tree_sitter_manager
from codegraphcontext.tools.languages.elixir import ElixirTreeSitterParser
from unittest.mock import MagicMock


class TestElixirParser:
    """Test the Elixir Parser logic."""

    @pytest.fixture(scope="class")
    def parser(self):
        manager = get_tree_sitter_manager()
        wrapper = MagicMock()
        wrapper.language_name = "elixir"
        wrapper.language = manager.get_language_safe("elixir")
        wrapper.parser = manager.create_parser("elixir")
        return ElixirTreeSitterParser(wrapper)

    def test_parse_simple_function(self, parser, temp_test_dir):
        """Parse a simple Elixir file and verify function extraction."""
        code = """
defmodule MyApp.Example do
  def hello(name) do
    "Hello, #{name}!"
  end
end
"""
        f = temp_test_dir / "test.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        assert "functions" in result
        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "hello"
        assert funcs[0]["args"] == ["name"]
        assert funcs[0]["visibility"] == "public"
        assert result["lang"] == "elixir"

    def test_parse_private_function(self, parser, temp_test_dir):
        """Parse private function definitions."""
        code = """
defmodule MyApp.Example do
  defp internal_helper(x) do
    x * 2
  end
end
"""
        f = temp_test_dir / "private.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        funcs = result["functions"]
        assert len(funcs) == 1
        assert funcs[0]["name"] == "internal_helper"
        assert funcs[0]["visibility"] == "private"

    def test_parse_module(self, parser, temp_test_dir):
        """Parse module definitions (treated as classes)."""
        code = """
defmodule MyApp.Greeter do
  def greet, do: "hello"
end
"""
        f = temp_test_dir / "module.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        assert "classes" in result
        classes = result["classes"]
        assert len(classes) == 1
        assert classes[0]["name"] == "MyApp.Greeter"
        assert classes[0]["kind"] == "module"

    def test_parse_protocol(self, parser, temp_test_dir):
        """Parse protocol definitions."""
        code = """
defprotocol Greetable do
  def greeting(thing)
end
"""
        f = temp_test_dir / "protocol.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        classes = result["classes"]
        assert len(classes) == 1
        assert classes[0]["name"] == "Greetable"
        assert classes[0]["kind"] == "protocol"

    def test_parse_imports(self, parser, temp_test_dir):
        """Parse alias, import, require, and use statements."""
        code = """
defmodule MyApp.Example do
  alias MyApp.Config
  import Enum, only: [map: 2]
  require Logger
  use GenServer
end
"""
        f = temp_test_dir / "imports.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        imports = result["imports"]
        import_types = {imp["import_type"] for imp in imports}
        assert "alias" in import_types
        assert "import" in import_types
        assert "require" in import_types
        assert "use" in import_types

    def test_parse_dot_calls(self, parser, temp_test_dir):
        """Parse dot-notation function calls."""
        code = """
defmodule MyApp.Example do
  def run do
    Logger.info("starting")
    GenServer.start_link(__MODULE__, [])
  end
end
"""
        f = temp_test_dir / "calls.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        calls = result["function_calls"]
        call_names = {c["full_name"] for c in calls}
        assert "Logger.info" in call_names
        assert "GenServer.start_link" in call_names

    def test_parse_module_attributes(self, parser, temp_test_dir):
        """Parse module attributes as variables."""
        code = """
defmodule MyApp.Example do
  @greeting "Hello"
  @timeout 5000
end
"""
        f = temp_test_dir / "attrs.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        variables = result["variables"]
        var_names = {v["name"] for v in variables}
        assert "@greeting" in var_names
        assert "@timeout" in var_names

    def test_parse_macros(self, parser, temp_test_dir):
        """Parse macro definitions."""
        code = """
defmodule MyApp.Macros do
  defmacro my_macro(expr) do
    quote do
      unquote(expr) + 1
    end
  end
end
"""
        f = temp_test_dir / "macros.ex"
        f.write_text(code)

        result = parser.parse(str(f))

        macros = result["macros"]
        assert len(macros) == 1
        assert macros[0]["name"] == "my_macro"
