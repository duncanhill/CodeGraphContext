
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from ..utils.debug_log import info_logger, error_logger, warning_logger
from ..scip.scip_pb2 import Index, Document, SymbolInformation, SymbolRole
from ..cli.config_manager import get_config_value

# SCIP Kind Enum Values (manually defined due to nested enum access issues)
Class = 7
Method = 26
Function = 17
Variable = 61
Interface = 21
Constructor = 9
Struct = 49
Enum = 11
Package = 43
Module = 38
Namespace = 31

# Map SCIP kinds to our Graph Labels
KIND_TO_LABEL = {
    0: "Symbol", # Fallback
    Class: "Class",
    Method: "Function",
    Function: "Function",
    Variable: "Variable",
    Interface: "Interface",
    Constructor: "Function",
    Struct: "Struct",
    Enum: "Enum",
    Package: "Module",
    Module: "Module",
    Namespace: "Module"
}

class ScipIndexer:
    def __init__(self):
        method = get_config_value("INDEXING_METHOD") or "tree-sitter"
        langs = get_config_value("SCIP_ENABLED_LANGUAGES") or "python"
        
        self.method = method
        self.enabled_langs = [l.strip() for l in langs.split(',')]
        
    def should_use_scip(self, lang: str) -> bool:
        """Check if SCIP should be used for this language."""
        if self.method == "auto":
            # Auto: use SCIP if available for the language
            return lang in self.enabled_langs
        elif self.method == "scip":
            # Force SCIP if enabled
            return lang in self.enabled_langs
        return False

    def run_index(self, repo_path: Path) -> Optional[Index]:
        """Runs the indexer for the repository if applicable."""
        # Currently we only support Python via scip-python
        if "python" in self.enabled_langs:
            # Check if repo has python files?
            # For now, just try running it if configured.
            return self._run_python_indexer(repo_path)
        return None

    def _run_python_indexer(self, repo_path: Path) -> Optional[Index]:
        info_logger(f"Running SCIP indexer for Python in {repo_path}")
        output_file = repo_path / "index.scip"
        try:
            # Run scip-python
            # We assume it is in the PATH
            cmd = ["scip-python", "index", "--cwd", str(repo_path), "--output", str(output_file)]
            
            # Simple check if there are python files to avoid empty run error
            has_py = any(repo_path.glob("**/*.py"))
            if not has_py:
                return None

            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            if not output_file.exists():
                error_logger(f"SCIP indexer failed to generate {output_file}")
                return None
            
            info_logger(f"Successfully generated SCIP index at {output_file}")
            
            index = Index()
            with open(output_file, "rb") as f:
                index.ParseFromString(f.read())
            
            return index
            
        except subprocess.CalledProcessError as e:
            # It might fail if no python code or dependency issues
            warning_logger(f"scip-python returned error: {e.stderr}")
            return None
        except Exception as e:
            error_logger(f"Error processing SCIP index: {e}")
            return None

    def ingest_index(self, driver, index: Index, repo_path: Path):
        """Ingests the SCIP index into Neo4j."""
        info_logger(f"Ingesting SCIP index ({len(index.documents)} documents) into graph...")
        
        try:
            symbol_info_map = {s.symbol: s for s in index.external_symbols}
            # Also map document local symbols (if any, though usually symbols are global-ish in SCIP)
            for doc in index.documents:
                for s in doc.symbols:
                    symbol_info_map[s.symbol] = s

            with driver.session() as session:
                for i, doc in enumerate(index.documents):
                    self._process_document(session, doc, repo_path, symbol_info_map)
                
                # Cleanup any local symbols that might have slipped through
                # This ensures the graph is clean even if filtering logic misses edge cases
                session.run("MATCH (n) WHERE n.scip_symbol STARTS WITH 'local' DETACH DELETE n")
            
        except Exception as e:
            error_logger(f"Error ingesting SCIP index: {e}")
            # We re-raise to let GraphBuilder know it failed, or we can suppress if we want partial success?
            # GraphBuilder catches it.
            raise e

    def _infer_label_from_symbol(self, symbol: str) -> str:
        """Infer graph label from SCIP symbol string syntax."""
        if symbol.endswith(").") or symbol.endswith("()"): # Corrected the typo from original instruction
            return "Function"
        if symbol.endswith("#"):
            return "Class"
        if symbol.endswith("/"):
            return "Module"
        if "package" in symbol and "module" in symbol:
             return "Module"
        return "Symbol"

    def _process_document(self, session, doc: Document, repo_path: Path, symbol_map: Dict[str, SymbolInformation]):
        # print(f"DEBUG: _process_document {doc.relative_path}")
        if doc.relative_path.startswith(".."):
            # Skip files outside the repository root (e.g. from sibling directories or site-packages)
            # print(f"DEBUG: Skipping external file: {doc.relative_path}")
            return

        file_path_str = str((repo_path / doc.relative_path).resolve())
        # file_path_str must match what GraphBuilder uses (absolute path)

        # 1. Create File Node
        # Ensure we set the name property for the file, as visualization relies on it.
        file_name = os.path.basename(file_path_str)
        session.run("""
            MERGE (f:File {path: $path})
            SET f.relative_path = $rel_path, f.name = $name
        """, path=file_path_str, rel_path=doc.relative_path, name=file_name)
        
        # 1.5 Link File to Repository/Directory Hierarchy
        try:
            # Get consistent absolute path string
            repo_path_str = str(repo_path.resolve())
            
            # doc.relative_path is like "module_c/submodule1.py"
            # Split into parts: ("module_c", "submodule1.py")
            relative_parts = Path(doc.relative_path).parts
            
            print(f"DEBUG_HIERARCHY: Processing {doc.relative_path}. Parts: {relative_parts}")
            
            # Start from the Repository root
            parent_path = repo_path_str
            parent_label = "Repository"
            
            # Iterate through directory parts only (exclude filename)
            # e.g. for "a/b/c.py", iterate ["a", "b"]
            for part in relative_parts[:-1]:
                current_abs_path = os.path.join(parent_path, part)
                
                # Match parent, create current directory, link them
                session.run(f"""
                    MATCH (p:{parent_label} {{path: $parent_path}})
                    MERGE (d:Directory {{path: $current_path}})
                    SET d.name = $part
                    MERGE (p)-[:CONTAINS]->(d)
                """, parent_path=parent_path, current_path=current_abs_path, part=part)
                
                # Move down for next iteration
                parent_path = current_abs_path
                parent_label = "Directory"
            
            # Finally, link the immediate parent (Repo or Directory) to the File
            session.run(f"""
                MATCH (p:{parent_label} {{path: $parent_path}})
                MATCH (f:File {{path: $file_path}})
                MERGE (p)-[:CONTAINS]->(f)
            """, parent_path=parent_path, file_path=file_path_str)
            
        except Exception as e:
            # Log but don't fail the whole file indexing if hierarchy fails
            print(f"DEBUG: Failed to link file hierarchy for {doc.relative_path}: {e}")
        
        # 1.5 Link File to Repository/Directory Hierarchy
        try:
            # The repo_path argument is a Path object for the root of the repository
            repo_path_str = str(repo_path.resolve())
            
            # doc.relative_path is relative to the SCIP index root (which is the repo root)
            # e.g., "module_c/submodule1.py" or "main.py"
            relative_path_str = doc.relative_path
            path_parts = Path(relative_path_str).parts
            
            parent_path = repo_path_str
            parent_label = "Repository"
            
            # Use 'repo_path_str' as the absolute base for forming directory paths
            current_abs_path = repo_path_str

            # Iterate over directories (all parts except the last one, which is the file)
            for part in path_parts[:-1]:
                current_abs_path = os.path.join(current_abs_path, part)
                
                session.run(f"""
                    MATCH (p:{parent_label} {{path: $parent_path}})
                    MERGE (d:Directory {{path: $current_path}})
                    SET d.name = $part
                    MERGE (p)-[:CONTAINS]->(d)
                """, parent_path=parent_path, current_path=current_abs_path, part=part)
                
                parent_path = current_abs_path
                parent_label = "Directory"
            
            # Verify if parent exists before linking
            # parent_check = session.run(f"MATCH (p:{parent_label} {{path: $path}}) RETURN count(p) as cnt", path=parent_path).single()
            # if parent_check['cnt'] == 0:
            #    print(f"DEBUG: Parent {parent_label} {parent_path} DOES NOT EXIST!")

            # Link the final parent (Repo or Directory) to the File
            session.run(f"""
                MATCH (p:{parent_label} {{path: $parent_path}})
                MATCH (f:File {{path: $file_path}})
                MERGE (p)-[:CONTAINS]->(f)
            """, parent_path=parent_path, file_path=file_path_str)
            
        except Exception as e:
            # Log but don't fail the whole file indexing if hierarchy fails
            print(f"DEBUG: Failed to link file hierarchy for {doc.relative_path}: {e}")
        
        # 2. Identify Definitions and their Ranges
        # We need to reconstruct the hierarchy (Context) to handle "CONTAINS"
        # SCIP ranges require interpretation: [start_line, start_char, end_line, end_char] or [start_line, start_char, end_char]
        
        # Sort occurrences by start position
        # Occurrences: repeated int32 range = 1;
        # SCIP specs: range is 3 or 4 elements.
        # If 3: start_line, start_char, end_char (end_line == start_line)
        # If 4: start_line, start_char, end_line, end_char
        # All 0-indexed.
        
        definitions = []
        references = []
        
        for occ in doc.occurrences:
            if occ.symbol_roles & SymbolRole.Definition:
                definitions.append(occ)
            else:
                references.append(occ)
        
        # Sort definitions by range start
        definitions.sort(key=lambda x: (x.range[0], x.range[1]))
        
        # Create Nodes for Definitions
        # We also need to map symbol -> node_id or path/name to link references later.
        # Ideally we store `scip_symbol` on the node.
        
        for occ in definitions:
            # Filter out local symbols from definitions
            if occ.symbol.startswith("local"):
                continue

            sym_info = symbol_map.get(occ.symbol)
            kind = sym_info.kind if sym_info else 0
            label = KIND_TO_LABEL.get(kind)
            
            # Print definitions for debugging
            # if "test_mixins" in doc.relative_path:
            #     print(f"DEBUG: Definition found: {occ.symbol}, Kind: {kind}, Label: {label}")
            
            # If label is missing or generic "Symbol", try to infer from string
            if not label or label == "Symbol":
                inferred = self._infer_label_from_symbol(occ.symbol)
                if inferred != "Symbol":
                    label = inferred

            if not label: 
                # Should not happen if we mapped 0 to "Symbol"
                label = "Symbol"
            
            name = self._extract_name(occ.symbol, sym_info)
            line_number = occ.range[0] + 1
            
            # Create Node
            # We use MERGE on scip_symbol if possible, or (path, symbol)
            # Existing schema uses (name, path, line_number) constraint.
            # We should try to align.
            
            try:
                session.run(f"""
                    MATCH (f:File {{path: $path}})
                    MERGE (n:{label} {{scip_symbol: $symbol}})
                    SET n.name = $name, n.path = $path, n.line_number = $line_number
                    MERGE (f)-[:CONTAINS]->(n)
                """, path=file_path_str, symbol=occ.symbol, name=name, line_number=line_number)
            except Exception as e:
                error_logger(f"Error creating definition node for {occ.symbol}: {e}")

        # 3. Process References (The "100% Accurate" Links)
        # To link a reference to its caller, we need to know which definition "contains" this reference location.
        # We can implement a simple hit-test against the definitions in this file.
        
        # Build an interval tree or simple list for hit-testing
        # (Definition Symbol, StartLine, EndLine)
        # Wait, definitions have ranges too.
        # A function definition range covers the whole body? 
        # SCIP definitions usually only cover the *identifier*, not the body.
        # Ah, that's a problem. SCIP Occurrences for Definition only point to the name key.
        # How do we know the scope?
        # SCIP *Documents* sometimes have `symbols` attached which might have range?
        # No, `SymbolInformation` doesn't have range.
        
        # However, `scip-python` (and others) usually emit an occurrence for the definition.
        # Does SCIP provide the *body* range?
        # Enclosing range?
        # It's not strictly in the core `Occurrence`.
        # Often it is inferred or there is a separte "scope" concept? 
        # Actually proper LSIF/SCIP indexers usually emit a "definition" occurrence for the name, 
        # and checking "enclosing" is harder without the full AST or explicit scope ranges.
        
        # But wait! If we want 100% accuracy, SCIP references link `Method A` calls `Method B`.
        # We need to know `Method A` contains the call.
        # If we can't determine the caller from SCIP, we lose the "Context" (Edges start from where?).
        
        # Standard SCIP usage: The "hovers" work because you ask "what is at this position".
        # To build a call graph `A -> B`, we need to know `Call to B` is inside `A`.
        # If `scip-python` doesn't give us body ranges, we might need to rely on indentation (Python) or braces.
        # OR, we can use our TreeSitter parser to get the ranges of functions, and then map SCIP occurrences to them!
        
        # HYBRID APPROACH:
        # 1. Use TreeSitter to identify Function Boundaries (StartLine, EndLine).
        # 2. Use SCIP to identify Reference Targets (at Line X, Call to Symbol Y).
        # 3. Match: Reference at Line X is inside Function that spans [Start, End].
        
        # 3. Process References (The "100% Accurate" Links)
        # We use a Hybrid Approach: Use TreeSitter to get function boundaries, then map SCIP references.
        
        # 3. Process References (The "100% Accurate" Links)
        # We use a Hybrid Approach: Use TreeSitter to get function boundaries, then map SCIP references.
        
        # Parse file with TreeSitter to identify function scopes
        # This helps us know "Function X calls Function Y" by checking if the call is within X's body.
        from .graph_builder import TreeSitterParser
        ts_parser = TreeSitterParser('python') # Since we only support python for now
        
        try:
            # We need the absolute path again
            file_path = repo_path / doc.relative_path
            
            # Skip if file doesn't exist (e.g. deleted but still in index?)
            if not file_path.exists(): 
                return

            # Simple parse to get function ranges
            parse_result = ts_parser.parse(file_path)
            functions = parse_result.get('functions', [])
            
            if not functions:
                return

            # functions list contains dicts with 'name', 'line_number', 'end_line'
            print(f"DEBUG: {doc.relative_path}: Found {len(functions)} functions, {len(references)} references")
            
            if not references:
                return

            for ref in references:
                ref_line = ref.range[0] + 1
                
                # logic check
                # print(f"DEBUG: Processing ref at line {ref_line}: {ref.symbol}")

                caller_function = None
                for func in functions:
                     if func['line_number'] <= ref_line <= func['end_line']:
                         caller_function = func
                         break
                
                # Skip local symbols (they are not useful for call graph and cause massive fan-in/fan-out)
                if ref.symbol.startswith("local"):
                    continue

                target_symbol = ref.symbol
                
                # Logic to handle nested functions
                containing_funcs = [f for f in functions if f['line_number'] <= ref_line <= f['end_line']]
                if not containing_funcs:
                    # print(f"DEBUG: Ref at {ref_line} not inside any function")
                    continue
                
                containing_funcs.sort(key=lambda f: f['end_line'] - f['line_number'])
                caller = containing_funcs[0]
                
                print(f"DEBUG: Found caller: {caller['name']} for ref {ref.symbol}")
                
                 # Check if it's an external symbol
                # For external symbols, we merge a "ghost" node or external node.
                # Ideally, we merge based on `scip_symbol` property.
                
                # We infer label for target if we have info, else "Symbol"
                target_info = symbol_map.get(target_symbol)
                target_kind = target_info.kind if target_info else 0
                target_label = KIND_TO_LABEL.get(target_kind, "Symbol")
                
                # If target label is generic, try to infer
                if target_label == "Symbol":
                     inferred = self._infer_label_from_symbol(target_symbol)
                     if inferred != "Symbol":
                         target_label = inferred

                # Cypher query to create relationship
                # We match the caller node by name and path.
                # Caller might be a Function or a Method.
                # We match generically and filter/assume correct node.
                
                try:
                    # Primary Match: Path and Name (and allow Function or Method label)
                    result = session.run(f"""
                        MATCH (caller {{path: $path, name: $caller_name}})
                        WHERE caller:Function OR caller:Method
                        MERGE (target:{target_label} {{scip_symbol: $target_symbol}})
                        ON CREATE SET target.name = $target_name
                        MERGE (caller)-[r:CALLS]->(target)
                        SET r.scip_verified = true
                        RETURN count(r) as created
                    """, 
                    path=file_path_str, 
                    caller_name=caller['name'], 
                    target_symbol=target_symbol,
                    target_name=self._extract_name(target_symbol, target_info)
                    )
                    
                    created = result.single()['created']
                    if created == 0:
                        # Fallback: Match by Line Number
                        # This works well when names differ slightly or extraction is tricky.
                        result_fallback = session.run(f"""
                            MATCH (caller {{path: $path, line_number: $line_number}})
                            WHERE caller:Function OR caller:Method
                            MERGE (target:{target_label} {{scip_symbol: $target_symbol}})
                            ON CREATE SET target.name = $target_name
                            MERGE (caller)-[r:CALLS]->(target)
                            SET r.scip_verified = true
                            RETURN count(r) as created
                        """,
                        path=file_path_str,
                        line_number=caller['line_number'],
                        target_symbol=target_symbol,
                        target_name=self._extract_name(target_symbol, target_info)
                        )
                        # if result_fallback.single()['created'] == 0:
                        #      warning_logger(f"Failed to link call from {caller['name']} to {target_symbol}")

                except Exception as e:
                    error_logger(f"Cypher error creating link: {e}")

        except ImportError:
            error_logger("Could not import TreeSitterParser. Ensure graph_builder is available.")
        except Exception as e:
            error_logger(f"Error linking SCIP references for {file_path_str}: {e}")
            # import traceback
            # traceback.print_exc()

    def _extract_name(self, symbol: str, sym_info: Optional[SymbolInformation]) -> str:
        if sym_info and sym_info.display_name:
            return sym_info.display_name
        
        # Robust extraction
        clean_symbol = symbol.replace('.', '/').replace('#', '/').replace('()', '')
        parts = [p for p in clean_symbol.split('/') if p]
        
        if parts:
            return parts[-1]
        return symbol # Fallback to full symbol if splitting fails
