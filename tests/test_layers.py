"""The layer rules from ARCHITECTURE.md, checked on the imports of every module."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "src" / "filehouse"

# layer -> top-level modules it must not import
FORBIDDEN = {
    "core": {"PySide6", "filehouse.qt_models", "filehouse.integration", "filehouse.ui"},
    "ui": {"gi"},
    "qt_models": {"filehouse.ui"},
    "integration": {"filehouse.ui"},
}


def imported_modules(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


@pytest.mark.parametrize("layer", FORBIDDEN)
def test_layer_imports(layer):
    files = list((PACKAGE / layer).rglob("*.py"))
    assert files
    for path in files:
        for module in imported_modules(path):
            for forbidden in FORBIDDEN[layer]:
                assert module != forbidden and not module.startswith(forbidden + "."), (
                    f"{path.relative_to(PACKAGE)} imports {module}"
                )
