"""Guard for the Palette OS plugin loader.

The OS loads every plugin into one Python process and, on each load, purges the
shared top-level names (backend, models, tools, schemas, services, db,
palette_sdk) from sys.modules. An import that runs at module load binds this
app's modules; one that runs later inside a function resolves to whichever
plugin loaded last. So every import of those names, and every relative import,
must sit at module level.
"""
import ast
from pathlib import Path

SHARED = {"backend", "models", "tools", "schemas", "services", "db", "palette_sdk"}
BACKEND = Path(__file__).resolve().parents[1]


def _deferred(path: Path) -> list[str]:
    found: list[str] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if in_function and isinstance(child, ast.ImportFrom):
                if child.level or (child.module or "").split(".")[0] in SHARED:
                    found.append(f"{path.relative_to(BACKEND.parent)}:{child.lineno}")
            elif in_function and isinstance(child, ast.Import):
                if any(a.name.split(".")[0] in SHARED for a in child.names):
                    found.append(f"{path.relative_to(BACKEND.parent)}:{child.lineno}")
            visit(child, in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))

    visit(ast.parse(path.read_text(encoding="utf-8")), False)
    return found


def test_shared_names_are_imported_at_module_level():
    offenders = [hit for p in sorted(BACKEND.rglob("*.py")) if "tests" not in p.parts and "__pycache__" not in p.parts
                 for hit in _deferred(p)]
    assert not offenders, "Move these imports to module level (Palette OS loader shares these names):\n" + "\n".join(offenders)
