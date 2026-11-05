"""The layering is a rule, so it is tested.

Domain must not import Qt, HTTP, PDF or Excel. The application must not import
concrete adapters. Domain and application tests run without those libraries.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "spec2test"

FORBIDDEN_IN_DOMAIN = {
    "PySide6", "PyQt5", "PyQt6", "requests", "httpx", "urllib", "fitz", "pymupdf",
    "PyPDF2", "pypdf", "pandas", "openpyxl", "yaml", "sklearn", "pydantic",
}
FORBIDDEN_IN_APPLICATION = {
    "PySide6", "PyQt5", "PyQt6", "requests", "httpx", "fitz", "pymupdf", "PyPDF2",
    "pypdf", "pandas", "openpyxl", "sklearn",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def relative_import_targets(path: Path) -> set[str]:
    """Package-internal import targets, e.g. ``domain.models`` or ``adapters.x``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            targets.add(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("spec2test."):
                    targets.add(alias.name[len("spec2test.") :])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "spec2test."
        ):
            targets.add(node.module[len("spec2test.") :])
    return targets


def python_files(folder: str) -> list[Path]:
    return sorted((SRC / folder).rglob("*.py"))


@pytest.mark.parametrize("path", python_files("domain"), ids=lambda p: p.name)
def test_domain_has_no_infrastructure_imports(path):
    offending = imported_modules(path) & FORBIDDEN_IN_DOMAIN
    assert not offending, f"{path.name} imports {sorted(offending)}"


@pytest.mark.parametrize("path", python_files("application"), ids=lambda p: p.name)
def test_application_has_no_infrastructure_imports(path):
    offending = imported_modules(path) & FORBIDDEN_IN_APPLICATION
    assert not offending, f"{path.name} imports {sorted(offending)}"


@pytest.mark.parametrize("path", python_files("application"), ids=lambda p: p.name)
def test_application_does_not_import_concrete_adapters(path):
    offending = {t for t in relative_import_targets(path) if t.startswith("adapters")}
    assert not offending, (
        f"{path.name} imports {sorted(offending)}; the application depends on ports only"
    )


@pytest.mark.parametrize("path", python_files("domain"), ids=lambda p: p.name)
def test_domain_depends_on_nothing_but_itself(path):
    offending = {
        target
        for target in relative_import_targets(path)
        if target.startswith(("application", "adapters", "ui"))
    }
    assert not offending, f"{path.name} imports {sorted(offending)}"


def test_domain_and_application_import_without_any_optional_library(monkeypatch):
    """Simulate a machine without Qt, requests, PyMuPDF, pandas and openpyxl."""
    blocked = ["PySide6", "requests", "fitz", "pymupdf", "pandas", "openpyxl", "sklearn"]
    for name in blocked:
        monkeypatch.setitem(sys.modules, name, None)
    for name in list(sys.modules):
        if name.startswith("spec2test"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    import importlib

    for module in (
        "spec2test.domain.models",
        "spec2test.domain.validation",
        "spec2test.application.contracts",
        "spec2test.application.chunking",
        "spec2test.application.generate",
        "spec2test.application.ingest",
        "spec2test.application.ports",
        "spec2test.application.prompts",
        "spec2test.application.assembly",
        "spec2test.application.legacy",
    ):
        importlib.import_module(module)


def test_qt_signals_and_http_objects_do_not_cross_the_port_boundary():
    from spec2test.application import ports

    source = (SRC / "application" / "ports.py").read_text(encoding="utf-8")
    assert "Signal" not in source
    assert "Response" not in source
    # the reply type is plain data
    reply = ports.ModelReply(text="x")
    assert isinstance(reply.text, str)
    assert not hasattr(reply, "raw_response")
