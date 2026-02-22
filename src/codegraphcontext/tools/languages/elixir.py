from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from codegraphcontext.utils.debug_log import debug_log, info_logger, error_logger, warning_logger, debug_logger
from codegraphcontext.utils.tree_sitter_manager import execute_query

ELIXIR_QUERIES = {
    "functions": """
        (call
            (identifier) @def_type
            (arguments
                (call
                    (identifier) @func_name
                    (arguments) @func_args))
            (#match? @def_type "^(def|defp)$")) @func_def
    """,
    "classes": """
        (call
            (identifier) @call_type
            (arguments
                (alias) @module_name)
            (#match? @call_type "^defmodule$")) @module_def
        (call
            (identifier) @proto_type
            (arguments
                (alias) @proto_name)
            (#match? @proto_type "^defprotocol$")) @proto_def
    """,
    "imports": """
        (call
            (identifier) @import_type
            (arguments) @import_args
            (#match? @import_type "^(alias|import|require|use)$")) @import_def
    """,
    "calls": """
        (call
            (dot
                left: (_) @receiver
                right: (identifier) @method)
            (arguments)? @call_args) @call_node
    """,
    "variables": """
        (unary_operator
            (call
                (identifier) @attr_name
                (arguments) @attr_value)) @module_attr
    """,
    "macros": """
        (call
            (identifier) @macro_type
            (arguments
                (call
                    (identifier) @macro_name
                    (arguments) @macro_args))
            (#match? @macro_type "^defmacro$")) @macro_def
    """,
}


class ElixirTreeSitterParser:
    """An Elixir-specific parser using tree-sitter."""

    def __init__(self, generic_parser_wrapper: Any):
        self.generic_parser_wrapper = generic_parser_wrapper
        self.language_name = "elixir"
        self.language = generic_parser_wrapper.language
        self.parser = generic_parser_wrapper.parser

    def _get_node_text(self, node: Any) -> str:
        return node.text.decode("utf-8")

    def _parse_arguments(self, args_text: str) -> list[str]:
        """Parse a comma-separated argument string, respecting nesting."""
        args_text = args_text.strip("()")
        if not args_text.strip():
            return []
        args = []
        depth = 0
        current = []
        for ch in args_text:
            if ch in ('(', '{', '['):
                depth += 1
                current.append(ch)
            elif ch in (')', '}', ']'):
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                arg = ''.join(current).strip()
                if arg:
                    args.append(arg)
                current = []
            else:
                current.append(ch)
        arg = ''.join(current).strip()
        if arg:
            args.append(arg)
        return args

    def _get_parent_context(self, node: Any, types: Tuple[str, ...] = ('defmodule', 'def', 'defp')):
        """Find parent context for Elixir constructs by walking up to enclosing call nodes."""
        curr = node.parent
        while curr:
            if curr.type == 'call':
                # Check if the first child identifier matches one of the target types
                for child in curr.children:
                    if child.type == 'identifier':
                        ident = self._get_node_text(child)
                        if ident in types:
                            # Find the name: for defmodule it's an alias, for def/defp it's a call identifier
                            name = None
                            for arg_child in curr.children:
                                if arg_child.type == 'arguments':
                                    for ac in arg_child.children:
                                        if ac.type == 'alias':
                                            name = self._get_node_text(ac)
                                            break
                                        elif ac.type == 'call':
                                            name_node = None
                                            for cc in ac.children:
                                                if cc.type == 'identifier':
                                                    name_node = cc
                                                    break
                                            if name_node:
                                                name = self._get_node_text(name_node)
                                            break
                                    break
                            if name:
                                return name, ident, curr.start_point[0] + 1
                        break
            curr = curr.parent
        return None, None, None

    def _calculate_complexity(self, node: Any) -> int:
        """Calculate cyclomatic complexity for Elixir constructs."""
        complexity_keywords = {
            "case", "cond", "if", "unless", "with", "try", "rescue", "catch",
        }
        complexity_operators = {"&&", "||", "and", "or"}
        count = 1

        def traverse(n):
            nonlocal count
            if n.type in complexity_keywords:
                count += 1
            elif n.type == 'identifier' and self._get_node_text(n) in complexity_keywords:
                count += 1
            elif n.type == 'binary_operator':
                op_text = self._get_node_text(n)
                for op in complexity_operators:
                    if op in op_text:
                        count += 1
                        break
            for child in n.children:
                traverse(child)

        traverse(node)
        return count

    def _get_docstring(self, node: Any) -> Optional[str]:
        """Extract @doc or @moduledoc module attributes before the node."""
        prev_sibling = node.prev_sibling
        while prev_sibling:
            if prev_sibling.type == 'unary_operator':
                text = self._get_node_text(prev_sibling)
                if text.startswith('@doc') or text.startswith('@moduledoc'):
                    return text.strip()
            elif prev_sibling.type in ('comment',):
                text = self._get_node_text(prev_sibling)
                if text.startswith('#'):
                    return text.strip()
            elif prev_sibling.type not in ('comment', 'unary_operator'):
                break
            prev_sibling = prev_sibling.prev_sibling
        return None

    def parse(self, path: Path, is_dependency: bool = False, index_source: bool = False) -> Dict[str, Any]:
        """Parses an Elixir file and returns its structure."""
        self.index_source = index_source
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            source_code = f.read()

        tree = self.parser.parse(bytes(source_code, "utf8"))
        root_node = tree.root_node

        functions = self._find_functions(root_node)
        classes = self._find_classes(root_node)
        imports = self._find_imports(root_node)
        function_calls = self._find_calls(root_node)
        variables = self._find_variables(root_node)
        macros = self._find_macros(root_node)

        return {
            "path": str(path),
            "functions": functions,
            "classes": classes,
            "variables": variables,
            "imports": imports,
            "function_calls": function_calls,
            "macros": macros,
            "is_dependency": is_dependency,
            "lang": self.language_name,
        }

    def _find_functions(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all function definitions (def/defp)."""
        functions = []
        query_str = ELIXIR_QUERIES["functions"]

        all_captures = list(execute_query(self.language, query_str, root_node))

        captures_by_func = {}
        for node, capture_name in all_captures:
            if capture_name == 'func_def':
                captures_by_func[id(node)] = {
                    'node': node, 'name': None, 'args': None, 'def_type': None
                }

        for node, capture_name in all_captures:
            for func_id, func_data in captures_by_func.items():
                func_node = func_data['node']
                if not (node.start_byte >= func_node.start_byte and
                        node.end_byte <= func_node.end_byte):
                    continue

                if capture_name == 'func_name':
                    captures_by_func[func_id]['name'] = self._get_node_text(node)
                elif capture_name == 'func_args':
                    captures_by_func[func_id]['args'] = self._get_node_text(node)
                elif capture_name == 'def_type':
                    captures_by_func[func_id]['def_type'] = self._get_node_text(node)

        for func_data in captures_by_func.values():
            func_node = func_data['node']
            name = func_data['name']

            if name:
                args_text = func_data['args'] or ""
                args = self._parse_arguments(args_text)
                def_type = func_data['def_type'] or "def"
                visibility = "private" if def_type == "defp" else "public"

                context, context_type, _ = self._get_parent_context(func_node, ('defmodule',))
                docstring = self._get_docstring(func_node)
                complexity = self._calculate_complexity(func_node)

                entry = {
                    "name": name,
                    "line_number": func_node.start_point[0] + 1,
                    "end_line": func_node.end_point[0] + 1,
                    "args": args,
                    "visibility": visibility,
                    "complexity": complexity,
                    "context": context,
                    "lang": self.language_name,
                    "is_dependency": False,
                }
                if self.index_source:
                    entry["source"] = self._get_node_text(func_node)
                    entry["docstring"] = docstring

                functions.append(entry)

        return functions

    def _find_classes(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all module and protocol definitions (treated as classes for the graph)."""
        classes = []
        query_str = ELIXIR_QUERIES["classes"]

        all_captures = list(execute_query(self.language, query_str, root_node))

        captures_by_class = {}
        for node, capture_name in all_captures:
            if capture_name in ('module_def', 'proto_def'):
                captures_by_class[id(node)] = {
                    'node': node, 'name': None, 'kind': capture_name
                }

        for node, capture_name in all_captures:
            if capture_name in ('module_name', 'proto_name'):
                for class_id, class_data in captures_by_class.items():
                    class_node = class_data['node']
                    if (node.start_byte >= class_node.start_byte and
                            node.end_byte <= class_node.end_byte):
                        captures_by_class[class_id]['name'] = self._get_node_text(node)
                        break

        for class_data in captures_by_class.values():
            class_node = class_data['node']
            name = class_data['name']

            if name:
                kind = "protocol" if class_data['kind'] == 'proto_def' else "module"
                context, context_type, _ = self._get_parent_context(class_node, ('defmodule',))
                docstring = self._get_docstring(class_node)

                entry = {
                    "name": name,
                    "line_number": class_node.start_point[0] + 1,
                    "end_line": class_node.end_point[0] + 1,
                    "bases": [],
                    "context": context,
                    "context_type": context_type,
                    "kind": kind,
                    "decorators": [],
                    "lang": self.language_name,
                    "is_dependency": False,
                }
                if self.index_source:
                    entry["source"] = self._get_node_text(class_node)
                    entry["docstring"] = docstring

                classes.append(entry)

        return classes

    def _find_imports(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all alias, import, require, and use statements."""
        imports = []
        query_str = ELIXIR_QUERIES["imports"]

        all_captures = list(execute_query(self.language, query_str, root_node))

        captures_by_import = {}
        for node, capture_name in all_captures:
            if capture_name == 'import_def':
                captures_by_import[id(node)] = {
                    'node': node, 'import_type': None, 'import_args': None
                }

        for node, capture_name in all_captures:
            for import_id, import_data in captures_by_import.items():
                import_node = import_data['node']
                if not (node.start_byte >= import_node.start_byte and
                        node.end_byte <= import_node.end_byte):
                    continue

                if capture_name == 'import_type':
                    captures_by_import[import_id]['import_type'] = self._get_node_text(node)
                elif capture_name == 'import_args':
                    captures_by_import[import_id]['import_args'] = self._get_node_text(node)

        for import_data in captures_by_import.values():
            import_node = import_data['node']
            import_type = import_data['import_type']
            import_args = import_data['import_args']

            if import_type and import_args:
                full_text = self._get_node_text(import_node)
                # Extract the module name from the arguments, stripping trailing options
                raw_args = import_args.strip().strip("()")
                # Take only the first argument (module name) before any comma-separated options
                parts = self._parse_arguments(raw_args)
                module_name = parts[0] if parts else raw_args

                imports.append({
                    "name": module_name,
                    "full_import_name": full_text,
                    "import_type": import_type,
                    "line_number": import_node.start_point[0] + 1,
                    "alias": None,
                    "lang": self.language_name,
                    "is_dependency": False,
                })

        return imports

    def _find_calls(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all function and method calls (dot calls)."""
        calls = []
        query_str = ELIXIR_QUERIES["calls"]

        all_captures = list(execute_query(self.language, query_str, root_node))

        captures_by_call = {}
        for node, capture_name in all_captures:
            if capture_name == 'call_node':
                captures_by_call[id(node)] = {
                    'node': node, 'receiver': None, 'method': None, 'args': []
                }

        for node, capture_name in all_captures:
            for call_id, call_data in captures_by_call.items():
                call_node = call_data['node']
                if not (node.start_byte >= call_node.start_byte and
                        node.end_byte <= call_node.end_byte):
                    continue

                if capture_name == 'receiver':
                    captures_by_call[call_id]['receiver'] = self._get_node_text(node)
                elif capture_name == 'method':
                    captures_by_call[call_id]['method'] = self._get_node_text(node)
                elif capture_name == 'call_args':
                    captures_by_call[call_id]['args'] = self._parse_arguments(
                        self._get_node_text(node)
                    )

        for call_data in captures_by_call.values():
            call_node = call_data['node']
            method = call_data['method']

            if method:
                receiver = call_data['receiver']
                full_name = f"{receiver}.{method}" if receiver else method

                context_name, context_type, context_line = self._get_parent_context(call_node)
                class_context = context_name if context_type == 'defmodule' else None
                if context_type in ('def', 'defp'):
                    enclosing_module, _, _ = self._get_parent_context(
                        call_node, ('defmodule',)
                    )
                    class_context = enclosing_module

                calls.append({
                    "name": method,
                    "full_name": full_name,
                    "line_number": call_node.start_point[0] + 1,
                    "args": call_data['args'],
                    "inferred_obj_type": None,
                    "context": (context_name, context_type, context_line),
                    "class_context": class_context,
                    "lang": self.language_name,
                    "is_dependency": False,
                })

        return calls

    def _find_variables(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all module attributes (@attr_name value)."""
        variables = []
        query_str = ELIXIR_QUERIES["variables"]

        all_captures = list(execute_query(self.language, query_str, root_node))

        captures_by_attr = {}
        for node, capture_name in all_captures:
            if capture_name == 'module_attr':
                captures_by_attr[id(node)] = {
                    'node': node, 'name': None, 'value': None
                }

        for node, capture_name in all_captures:
            for attr_id, attr_data in captures_by_attr.items():
                attr_node = attr_data['node']
                if not (node.start_byte >= attr_node.start_byte and
                        node.end_byte <= attr_node.end_byte):
                    continue

                if capture_name == 'attr_name':
                    captures_by_attr[attr_id]['name'] = self._get_node_text(node)
                elif capture_name == 'attr_value':
                    captures_by_attr[attr_id]['value'] = self._get_node_text(node)

        for attr_data in captures_by_attr.values():
            name = attr_data['name']
            value = attr_data['value']

            if name:
                context, context_type, _ = self._get_parent_context(
                    attr_data['node'], ('defmodule',)
                )

                variables.append({
                    "name": f"@{name}",
                    "line_number": attr_data['node'].start_point[0] + 1,
                    "value": value,
                    "type": "module_attribute",
                    "context": context,
                    "class_context": context,
                    "lang": self.language_name,
                    "is_dependency": False,
                })

        return variables

    def _find_macros(self, root_node: Any) -> list[Dict[str, Any]]:
        """Find all macro definitions (defmacro)."""
        macros = []
        query_str = ELIXIR_QUERIES["macros"]

        all_captures = list(execute_query(self.language, query_str, root_node))

        captures_by_macro = {}
        for node, capture_name in all_captures:
            if capture_name == 'macro_def':
                captures_by_macro[id(node)] = {
                    'node': node, 'name': None, 'args': None
                }

        for node, capture_name in all_captures:
            for macro_id, macro_data in captures_by_macro.items():
                macro_node = macro_data['node']
                if not (node.start_byte >= macro_node.start_byte and
                        node.end_byte <= macro_node.end_byte):
                    continue

                if capture_name == 'macro_name':
                    captures_by_macro[macro_id]['name'] = self._get_node_text(node)
                elif capture_name == 'macro_args':
                    captures_by_macro[macro_id]['args'] = self._get_node_text(node)

        for macro_data in captures_by_macro.values():
            macro_node = macro_data['node']
            name = macro_data['name']

            if name:
                args_text = macro_data['args'] or ""
                args = self._parse_arguments(args_text)

                context, context_type, _ = self._get_parent_context(
                    macro_node, ('defmodule',)
                )
                docstring = self._get_docstring(macro_node)

                entry = {
                    "name": name,
                    "line_number": macro_node.start_point[0] + 1,
                    "end_line": macro_node.end_point[0] + 1,
                    "args": args,
                    "context": context,
                    "lang": self.language_name,
                    "is_dependency": False,
                }
                if self.index_source:
                    entry["source"] = self._get_node_text(macro_node)
                    entry["docstring"] = docstring

                macros.append(entry)

        return macros


def pre_scan_elixir(files: list[Path], parser_wrapper) -> dict:
    """Scans Elixir files to create a map of module/function names to their file paths."""
    imports_map = {}
    query_str = """
        (call
            (identifier) @call_type
            (arguments
                (alias) @name)
            (#match? @call_type "^defmodule$"))
        (call
            (identifier) @def_type
            (arguments
                (call
                    (identifier) @name))
            (#match? @def_type "^(def|defp)$"))
    """

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = parser_wrapper.parser.parse(bytes(f.read(), "utf8"))

            for capture, cap_name in execute_query(parser_wrapper.language, query_str, tree.root_node):
                if cap_name == 'name':
                    name = capture.text.decode('utf-8')
                    if name not in imports_map:
                        imports_map[name] = []
                    imports_map[name].append(str(path.resolve()))
        except Exception as e:
            warning_logger(f"Tree-sitter pre-scan failed for {path}: {e}")

    return imports_map
