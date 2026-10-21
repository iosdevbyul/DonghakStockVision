"""Exercise live source boundaries and prove that legacy debt cannot hide additions."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.architecture.checks import PACKAGE, Edge, compare, scan, violations

ROOT = Path(__file__).resolve().parents[2]


def sources(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / PACKAGE
    root.mkdir()
    (root / "__init__.py").write_text("")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in path.parents:
            if parent == root:
                break
            (parent / "__init__.py").touch()
        path.write_text(text)
    return root


def debt(edges: tuple[Edge, ...], modules: set[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count, "reason": "Synthetic existing debt"}
        for key, count in sorted(Counter(v.key for v in violations(modules, edges)).items())
    ]


def test_live_production_source_matches_only_reviewed_debt() -> None:
    modules, edges = scan(ROOT / "src" / PACKAGE)
    baseline = json.loads((ROOT / "docs" / "architecture-debt.json").read_text())
    assert baseline["schema_version"] == 1
    assert modules and edges
    failures = compare(violations(modules, edges), baseline["violations"])
    assert not failures, "Architecture boundary regression:\n" + "\n".join(failures)


@pytest.mark.parametrize(
    "statement,rule",
    [
        ("import sqlite3", "infrastructure-in-domain"),
        ("import fastapi as web", "infrastructure-in-domain"),
        ("from urllib.request import urlopen", "infrastructure-in-domain"),
        ("import socket", "infrastructure-in-domain"),
        ("from arbitrary_new_broker_sdk import Client", "external-in-domain"),
        ("from donghak_stock_vision.storage.sqlite import Store", "domain-direction"),
        ("from donghak_stock_vision.api.app import create_app", "domain-direction"),
        ("from ..storage import sqlite", "domain-direction"),
        ("import donghak_stock_vision.storage.sqlite as db", "domain-direction"),
    ],
)
def test_domain_cannot_gain_infrastructure(tmp_path: Path, statement: str, rule: str) -> None:
    root = sources(
        tmp_path, {"trading/new_engine.py": statement, "storage/sqlite.py": "", "api/app.py": ""}
    )
    modules, edges = scan(root)
    issues = violations(modules, edges)
    assert any(v.rule == rule and v.edge.source.endswith(".new_engine") for v in issues)
    assert any("NEW violation" in failure for failure in compare(issues, []))


def test_scanning_never_executes_source_or_module_initializers(tmp_path: Path) -> None:
    root = sources(
        tmp_path, {"trading/risk.py": "raise RuntimeError('never import me')\nimport sqlite3"}
    )
    (root / "__init__.py").write_text("raise RuntimeError('initializer must not execute')")
    modules, edges = scan(root)
    assert any(v.edge.target == "sqlite3" for v in violations(modules, edges))


@pytest.mark.parametrize(
    "updated",
    [
        "from donghak_stock_vision.storage.sqlite import Store, NewStore",
        "from donghak_stock_vision.storage.sqlite import Store as OtherName",
        (
            "from donghak_stock_vision.storage.sqlite import Store\n"
            "from donghak_stock_vision.storage.sqlite import Store"
        ),
        "def later():\n    from donghak_stock_vision.storage.sqlite import Store",
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    from donghak_stock_vision.storage.sqlite import Store"
        ),
    ],
)
def test_exact_legacy_import_cannot_exempt_new_symbols_scope_or_occurrences(
    tmp_path: Path, updated: str
) -> None:
    statement = "from donghak_stock_vision.storage.sqlite import Store"
    root = sources(tmp_path, {"trading/risk.py": statement, "storage/sqlite.py": ""})
    modules, edges = scan(root)
    baseline = debt(edges, modules)
    assert compare(violations(modules, edges), baseline) == []
    (root / "trading/risk.py").write_text(updated)
    modules, edges = scan(root)
    assert any(
        "NEW violation" in failure for failure in compare(violations(modules, edges), baseline)
    )


def test_line_moves_are_not_blanket_exemptions_and_removed_debt_is_explicit(tmp_path: Path) -> None:
    root = sources(tmp_path, {"trading/risk.py": "import sqlite3"})
    modules, edges = scan(root)
    baseline = debt(edges, modules)
    (root / "trading/risk.py").write_text("\n# harmless line move\nimport sqlite3\n")
    modules, edges = scan(root)
    assert compare(violations(modules, edges), baseline) == []
    (root / "trading/risk.py").write_text("")
    modules, edges = scan(root)
    assert any("STALE debt" in failure for failure in compare(violations(modules, edges), baseline))


@pytest.mark.parametrize(
    "statement",
    [
        "import importlib\nimportlib.import_module('donghak_stock_vision.storage.sqlite')",
        "import importlib as il\nil.import_module('donghak_stock_vision.storage.sqlite')",
        "from importlib import import_module as load\nload('donghak_stock_vision.storage.sqlite')",
        "__import__('donghak_stock_vision.storage.sqlite')",
        (
            "import importlib\nimportlib.import_module('..storage.sqlite', "
            "package='donghak_stock_vision.trading')"
        ),
        (
            "import importlib\n"
            "if importlib.import_module('donghak_stock_vision.storage.sqlite'):\n    pass"
        ),
    ],
)
def test_literal_dynamic_imports_resolve_without_execution(tmp_path: Path, statement: str) -> None:
    root = sources(tmp_path, {"trading/risk.py": statement, "storage/sqlite.py": ""})
    modules, edges = scan(root)
    assert any(
        v.rule == "domain-direction" and v.edge.target.endswith(".sqlite")
        for v in violations(modules, edges)
    )


@pytest.mark.parametrize(
    "statement",
    [
        "import importlib\nimportlib.import_module(name)",
        "__import__(name)",
        "import importlib\nimportlib.import_module('.child', package=package)",
    ],
)
def test_unresolved_dynamic_imports_require_review(tmp_path: Path, statement: str) -> None:
    root = sources(tmp_path, {"trading/risk.py": statement})
    modules, edges = scan(root)
    assert any(v.rule == "unresolved-dynamic-import" for v in violations(modules, edges))


def test_runtime_cycles_include_lazy_function_imports(tmp_path: Path) -> None:
    root = sources(
        tmp_path,
        {"trading/a.py": "from . import b", "trading/b.py": "def later():\n    from . import a"},
    )
    modules, edges = scan(root)
    cycles = [v for v in violations(modules, edges) if v.rule == "runtime-cycle-edge"]
    assert len(cycles) == 2 and any(v.edge.deferred for v in cycles)
    baseline = debt(edges, modules)
    (root / "trading/a.py").write_text("from . import b\nfrom . import b")
    modules, edges = scan(root)
    assert any(
        "NEW violation" in failure for failure in compare(violations(modules, edges), baseline)
    )


def test_type_only_links_are_visible_without_false_runtime_cycles(tmp_path: Path) -> None:
    root = sources(
        tmp_path,
        {
            "trading/a.py": "import typing as t\nif t.TYPE_CHECKING:\n    from . import b",
            "trading/b.py": "from . import a",
        },
    )
    modules, edges = scan(root)
    assert any(e.type_only and e.target.endswith(".b") for e in edges)
    assert not any(v.rule == "runtime-cycle-edge" for v in violations(modules, edges))


def test_package_initializer_participates_in_cycle_analysis(tmp_path: Path) -> None:
    root = sources(tmp_path, {"trading/a.py": ""})
    (root / "trading/__init__.py").write_text("from . import a")
    modules, edges = scan(root)
    assert any(v.rule == "runtime-cycle-edge" for v in violations(modules, edges))


def test_reverse_application_dependency_and_missing_local_module(tmp_path: Path) -> None:
    root = sources(
        tmp_path,
        {
            "models/new.py": (
                "from donghak_stock_vision.api.app import create_app\nfrom .missing import x"
            ),
            "api/app.py": "",
        },
    )
    modules, edges = scan(root)
    assert {v.rule for v in violations(modules, edges)} == {
        "domain-direction",
        "unresolved-local-import",
    }


def test_new_physical_domain_is_not_silently_ignored(tmp_path: Path) -> None:
    root = sources(tmp_path, {"new_unclassified/worker.py": ""})
    with pytest.raises(ValueError, match="Unclassified production module"):
        scan(root)


@pytest.mark.parametrize(
    "entry",
    [
        {"key": "x", "count": True, "reason": "debt"},
        {"key": "x", "count": 0, "reason": "debt"},
        {"key": "x", "count": 1, "reason": ""},
        {"key": "x", "count": 1, "reason": "debt", "wildcard": True},
    ],
)
def test_debt_entries_are_narrow_and_documented(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="Invalid architecture debt"):
        compare((), [entry])


def test_console_scripts_resolve_to_existing_application_functions() -> None:
    import ast
    import tomllib

    from tests.architecture.checks import Domain, domain

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    modules, _ = scan(ROOT / "src" / PACKAGE)
    for command, entrypoint in scripts.items():
        module, function = entrypoint.split(":")
        assert module in modules, f"Unresolved console entrypoint {command}: {entrypoint}"
        assert domain(module) is Domain.APPLICATION
        path = ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
        names = {n.name for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)}
        assert function in names, f"Unresolved CLI callable {command}: {entrypoint}"


def test_duplicate_module_identities_are_not_overwritten(tmp_path: Path) -> None:
    root = sources(tmp_path, {"trading/item.py": "", "trading/item/__init__.py": ""})
    with pytest.raises(ValueError, match="Duplicate module identity"):
        scan(root)


def test_syntax_errors_cannot_hide_dependencies(tmp_path: Path) -> None:
    root = sources(tmp_path, {"trading/risk.py": "def broken("})
    with pytest.raises(SyntaxError):
        scan(root)


def test_type_checking_else_branch_is_runtime(tmp_path: Path) -> None:
    root = sources(
        tmp_path,
        {
            "trading/a.py": (
                "from typing import TYPE_CHECKING as TC\n"
                "if TC:\n    pass\nelse:\n    from . import b"
            ),
            "trading/b.py": "from . import a",
        },
    )
    modules, edges = scan(root)
    assert len([v for v in violations(modules, edges) if v.rule == "runtime-cycle-edge"]) == 2
