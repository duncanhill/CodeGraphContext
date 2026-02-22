from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from codegraphcontext.utils.debug_log import debug_log, info_logger, error_logger, warning_logger, debug_logger
from codegraphcontext.utils.tree_sitter_manager import execute_query

HEEX_QUERIES = {
    "components": """
        (component) @component
    """,
    "tags": """
        (tag) @tag
    """,
    "directives": """
        (directive) @directive
    """,
    "slots": """
        (slot) @slot
    """,
    "component_names": """
        (component_name) @comp_name
    """,
    "attributes": """
        (attribute
            (attribute_name) @attr_name
        ) @attr
    """,
}


class HeexTreeSitterParser:
    """A HEEx (HTML+EEx) template-specific parser using tree-sitter."""

    def __init__(self, generic_parser_wrapper: Any):
        self.generic_parser_wrapper = generic_parser_wrapper
        self.language_name = "heex"
        self.language = generic_parser_wrapper.language
        self.parser = generic_parser_wrapper.parser

    def _get_node_text(self, node: Any) -> str:
        return node.text.decode("utf-8")

    def _get_parent_context(self, node: Any, types: Tuple[str, ...] = ('component', 'tag')):
        """Find parent context for HEEx constructs."""
        curr = node.parent
        while curr:
            if curr.type in types:
                # Try to find a component_name or tag_name child
                for child in curr.children:
                    if child.type in ('start_component', 'self_closing_component'):
                        for gc in child.children:
                            if gc.type == 'component_name':
                                return self._get_node_text(gc), curr.type, curr.start_point[0] + 1
                    elif child.type == 'start_tag':
                        for gc in child.children:
                            if gc.type == 'tag_name':
                                return self._get_node_text(gc), curr.type, curr.start_point[0] + 1
            curr = curr.parent
        return None, None, None

    def _find_components(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all component usages in the template."""
        components = []
        query_str = HEEX_QUERIES["components"]

        for node, cap in execute_query(self.language, query_str, root_node):
            if cap == 'component':
                comp_name = None
                for child in node.children:
                    if child.type in ('start_component', 'self_closing_component'):
                        for gc in child.children:
                            if gc.type == 'component_name':
                                comp_name = self._get_node_text(gc)
                                break
                        break

                if comp_name:
                    entry = {
                        "name": comp_name,
                        "line_number": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "lang": self.language_name,
                        "is_dependency": False,
                    }
                    if self.index_source:
                        entry["source"] = self._get_node_text(node)

                    components.append(entry)

        return components

    def _find_tags(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all HTML tags in the template."""
        tags = []
        query_str = HEEX_QUERIES["tags"]

        for node, cap in execute_query(self.language, query_str, root_node):
            if cap == 'tag':
                tag_name = None
                for child in node.children:
                    if child.type == 'start_tag':
                        for gc in child.children:
                            if gc.type == 'tag_name':
                                tag_name = self._get_node_text(gc)
                                break
                        break

                if tag_name:
                    entry = {
                        "name": tag_name,
                        "line_number": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "lang": self.language_name,
                        "is_dependency": False,
                    }
                    if self.index_source:
                        entry["source"] = self._get_node_text(node)

                    tags.append(entry)

        return tags

    def _find_directives(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all EEx directives/expressions in the template."""
        directives = []
        query_str = HEEX_QUERIES["directives"]

        for node, cap in execute_query(self.language, query_str, root_node):
            if cap == 'directive':
                text = self._get_node_text(node)
                directives.append({
                    "name": text.strip(),
                    "line_number": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "lang": self.language_name,
                    "is_dependency": False,
                })

        return directives

    def _find_slots(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all slot definitions in the template."""
        slots = []
        query_str = HEEX_QUERIES["slots"]

        for node, cap in execute_query(self.language, query_str, root_node):
            if cap == 'slot':
                slot_name = None
                for child in node.children:
                    if child.type == 'start_slot':
                        for gc in child.children:
                            if gc.type == 'slot_name':
                                slot_name = self._get_node_text(gc)
                                break
                        break

                if slot_name:
                    entry = {
                        "name": slot_name,
                        "line_number": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "lang": self.language_name,
                        "is_dependency": False,
                    }
                    if self.index_source:
                        entry["source"] = self._get_node_text(node)

                    slots.append(entry)

        return slots

    def _find_imports(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find component references that act as imports (module-qualified components)."""
        imports = []
        seen = set()
        query_str = HEEX_QUERIES["component_names"]

        for node, cap in execute_query(self.language, query_str, root_node):
            if cap == 'comp_name':
                comp_text = self._get_node_text(node)
                # Module-qualified components like MyAppWeb.Components.header
                if '.' in comp_text and not comp_text.startswith('.'):
                    # Extract module part
                    parts = comp_text.rsplit('.', 1)
                    module_name = parts[0]
                    if module_name not in seen:
                        seen.add(module_name)
                        imports.append({
                            "name": module_name,
                            "full_import_name": comp_text,
                            "line_number": node.start_point[0] + 1,
                            "alias": None,
                            "lang": self.language_name,
                            "is_dependency": False,
                        })

        return imports

    def parse(self, path: Path, is_dependency: bool = False, index_source: bool = False) -> Dict[str, Any]:
        """Parses a HEEx template file and returns its structure."""
        self.index_source = index_source
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            source_code = f.read()

        tree = self.parser.parse(bytes(source_code, "utf8"))
        root_node = tree.root_node

        components = self._find_components(root_node)
        tags = self._find_tags(root_node)
        directives = self._find_directives(root_node)
        slots = self._find_slots(root_node)
        imports = self._find_imports(root_node)

        return {
            "path": str(path),
            "functions": components,  # Components map to functions in the graph
            "classes": [],
            "variables": directives,  # Directives/expressions map to variables
            "imports": imports,
            "function_calls": [],
            "is_dependency": is_dependency,
            "lang": self.language_name,
        }


def pre_scan_heex(files: list[Path], parser_wrapper) -> dict:
    """Scans HEEx files to create a map of component names to their file paths."""
    imports_map = {}
    query_str = """
        (component_name) @comp_name
    """

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = parser_wrapper.parser.parse(bytes(f.read(), "utf8"))

            for capture, cap_name in execute_query(parser_wrapper.language, query_str, tree.root_node):
                if cap_name == 'comp_name':
                    name = capture.text.decode('utf-8')
                    if name not in imports_map:
                        imports_map[name] = []
                    imports_map[name].append(str(path.resolve()))
        except Exception as e:
            warning_logger(f"Tree-sitter pre-scan failed for {path}: {e}")

    return imports_map
