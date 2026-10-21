"""Repository-local AST boundary checker. No importlib or production imports."""

import ast
import json
import sys
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

PACKAGE = "donghak_stock_vision"


class Domain(Enum):
    CORE = "Core Contracts"
    MARKET = "Market Data"
    RESEARCH = "Research & Strategy"
    SIMULATION = "Simulation & Backtesting"
    PORTFOLIO = "Portfolio & Accounting"
    RISK = "Risk & Execution"
    GOVERNANCE = "Governance & Evidence"
    INFRA = "Infrastructure Adapters"
    APPLICATION = "Application / API / CLI"


CORE = {
    "data.learning",
    "data.schema",
    "data.research_calendar",
    "data.research_split",
    "providers.base",
    "storage.base",
    "strategy.contracts",
    "signals.domain",
    "backtest.contracts",
    "backtest.validation",
    "backtest.clock",
    "backtest.data",
    "backtest.entry_contract",
    "backtest.research_contract",
    "trading.domain",
}
PORTFOLIO = {"ledger", "position_history", "performance", "baseline_book", "baseline_costs"}
RISK = {"account", "execution", "historical_execution", "decision_bridge"}
GOVERNANCE = {
    "official_evidence",
    "dart_evidence",
    "corporate_action_coverage",
    "security_lifecycle",
    "corporate_action_document_review",
    "corporate_action_semantic_review",
    "corporate_action_acquisition",
    "run_evidence",
    "evidence_package",
    "runner_tail",
    "receipt_archive",
    "receipt_validation",
    "shared_evidence",
}
# These existing backtest modules also expose python -m CLIs; this list is not
# inferred from a __main__ block, which would let new domain code bypass rules.
BACKTEST_APPS = {
    "inspect_evidence",
    "verify_corporate_action_semantic_review",
    "collect_corporate_action_documents",
    "dart_multi_batch_session",
    "dart_batch_orchestrator",
    "verify_official",
    "review_corporate_action_documents",
    "verify_security_lifecycle",
    "verify_corporate_action_acquisition",
    "collect_dart",
    "dart_document_batch",
    "package_evidence",
    "verify_dart",
    "verify_runner_tail",
}
ALLOWED = {
    Domain.CORE: {Domain.CORE},
    Domain.MARKET: {Domain.CORE, Domain.MARKET},
    Domain.RESEARCH: {Domain.CORE, Domain.MARKET, Domain.RESEARCH, Domain.GOVERNANCE},
    Domain.PORTFOLIO: {Domain.CORE, Domain.MARKET, Domain.PORTFOLIO, Domain.GOVERNANCE},
    Domain.RISK: {Domain.CORE, Domain.MARKET, Domain.PORTFOLIO, Domain.RISK, Domain.GOVERNANCE},
    Domain.GOVERNANCE: {Domain.CORE, Domain.GOVERNANCE},
    Domain.SIMULATION: {
        Domain.CORE,
        Domain.MARKET,
        Domain.RESEARCH,
        Domain.SIMULATION,
        Domain.PORTFOLIO,
        Domain.RISK,
        Domain.GOVERNANCE,
    },
    Domain.INFRA: set(Domain) - {Domain.APPLICATION},
    Domain.APPLICATION: set(Domain),
}
# Capabilities, not every stdlib import: pathlib/json are used by existing pure
# evidence projections too. This is dependency checking, not a Python sandbox.
INFRA_ROOTS = {
    "sqlite3",
    "httpx",
    "requests",
    "aiohttp",
    "urllib3",
    "sqlalchemy",
    "boto3",
    "botocore",
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "alpaca",
    "ib_insync",
    "ibapi",
    "ccxt",
    "socket",
}


def domain(module: str) -> Domain:
    if module == PACKAGE:
        return Domain.CORE
    short = module.removeprefix(PACKAGE + ".")
    parts = short.split(".")
    if (
        short in CORE
        or parts[-1] in {"contracts", "domain", "schema"}
        or parts[-1].endswith("_contract")
    ):
        return Domain.CORE
    if len(parts) == 1 and (short in {"cli", "__main__"} or short.endswith("_cli")):
        return Domain.APPLICATION
    if parts[0] in {"api", "config"}:
        return Domain.APPLICATION
    if parts[0] in {"providers", "storage"} or short in {
        "strategy.adapter",
        "models.artifact_resolver",
        "backtest.dart_transport",
    }:
        return Domain.INFRA
    if parts[0] in {"data", "features", "validation", "ingestion"}:
        return Domain.MARKET
    if parts[0] in {"models", "signals", "strategy"}:
        return Domain.RESEARCH
    if parts[0] == "trading":
        return Domain.RISK
    if parts[0] == "backtest":
        if parts[-1] in PORTFOLIO:
            return Domain.PORTFOLIO
        if parts[-1] in RISK:
            return Domain.RISK
        if parts[-1] in BACKTEST_APPS:
            return Domain.APPLICATION
        if parts[-1] in GOVERNANCE:
            return Domain.GOVERNANCE
        return Domain.SIMULATION
    raise ValueError(f"Unclassified production module {module}; declare its domain explicitly")


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    statement: str
    scope: str
    type_only: bool
    deferred: bool
    line: int

    def key(self, rule: str) -> str:
        # No line-number exemption: harmless formatting can move a statement.
        # Full AST includes imported symbols/aliases; new occurrences are counted.
        return json.dumps(
            [
                rule,
                self.source,
                self.target,
                self.statement,
                self.scope,
                self.type_only,
                self.deferred,
            ],
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class Violation:
    rule: str
    edge: Edge

    @property
    def key(self) -> str:
        return self.edge.key(self.rule)

    def describe(self) -> str:
        return (
            f"{self.edge.source}:{self.edge.line} [{self.rule}] "
            f"{self.edge.target} ({self.edge.scope})"
        )


class Scanner(ast.NodeVisitor):
    def __init__(self, module: str, package: str, modules: set[str]) -> None:
        self.module, self.package, self.modules = module, package, modules
        self.edges: list[Edge] = []
        self.scope: list[str] = []
        self.function_depth = 0
        self.type_only = False
        self.typing_names = {"typing"}
        self.type_names = {"TYPE_CHECKING"}
        self.importlib_names = {"importlib"}
        self.loader_names = {"__import__"}

    def add(self, node: ast.Import | ast.ImportFrom | ast.Call, target: str) -> None:
        self.edges.append(
            Edge(
                self.module,
                target,
                ast.dump(node, include_attributes=False),
                ".".join(self.scope) or "<module>",
                self.type_only,
                self.function_depth > 0,
                node.lineno,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "typing":
                self.typing_names.add(alias.asname or alias.name)
            if alias.name == "importlib":
                self.importlib_names.add(alias.asname or alias.name)
        for target in sorted({a.name for a in node.names}):
            self.add(node, target)

    def relative(self, name: str, level: int) -> str:
        if not level:
            return name
        parts = self.package.split(".")
        if level > len(parts):
            raise ValueError(f"Relative import escapes package in {self.module}")
        return ".".join(parts[: len(parts) - level + 1] + ([name] if name else []))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self.relative(node.module or "", node.level)
        for alias in node.names:
            if base == "typing" and alias.name == "TYPE_CHECKING":
                self.type_names.add(alias.asname or alias.name)
            if base == "importlib" and alias.name == "import_module":
                self.loader_names.add(alias.asname or alias.name)
        targets = {
            f"{base}.{a.name}" if f"{base}.{a.name}" in self.modules else base for a in node.names
        }
        for target in sorted(targets):
            self.add(node, target)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        test = node.test
        negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
        if negated:
            assert isinstance(test, ast.UnaryOp)
            test = test.operand
        known = (isinstance(test, ast.Name) and test.id in self.type_names) or (
            isinstance(test, ast.Attribute)
            and isinstance(test.value, ast.Name)
            and test.value.id in self.typing_names
            and test.attr == "TYPE_CHECKING"
        )
        previous = self.type_only
        for children, type_branch in ((node.body, not negated), (node.orelse, negated)):
            self.type_only = previous or (known and type_branch)
            for child in children:
                self.visit(child)
        self.type_only = previous

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Definition-time decorators/defaults/annotations are still visited.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns:
            self.visit(node.returns)
        self.scope.append(node.name)
        self.function_depth += 1
        for child in node.body:
            self.visit(child)
        self.function_depth -= 1
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for value in [*node.bases, *node.decorator_list, *node.keywords]:
            self.visit(value)
        self.scope.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        loader = (isinstance(node.func, ast.Name) and node.func.id in self.loader_names) or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.importlib_names
            and node.func.attr == "import_module"
        )
        if loader:
            argument = (
                node.args[0]
                if node.args
                else next((k.value for k in node.keywords if k.arg == "name"), None)
            )
            target = (
                argument.value
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                else "<dynamic>"
            )
            if target.startswith("."):
                # Relative dynamic imports depend on an explicit package argument.
                pkg = (
                    node.args[1]
                    if len(node.args) > 1
                    else next((k.value for k in node.keywords if k.arg == "package"), None)
                )
                if isinstance(pkg, ast.Constant) and isinstance(pkg.value, str):
                    level = len(target) - len(target.lstrip("."))
                    parts = pkg.value.split(".")
                    tail = target.lstrip(".")
                    target = (
                        ".".join(parts[: len(parts) - level + 1] + ([tail] if tail else []))
                        if level <= len(parts)
                        else "<dynamic>"
                    )
                else:
                    target = "<dynamic>"
            self.add(node, target)
        self.generic_visit(node)


def scan(root: Path) -> tuple[set[str], tuple[Edge, ...]]:
    paths: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join([PACKAGE, *parts])
        if module in paths:
            raise ValueError(f"Duplicate module identity {module}: {paths[module]} and {path}")
        paths[module] = path
    modules = set(paths)
    edges: list[Edge] = []
    for module, path in paths.items():
        domain(module)  # New physical packages must be assigned, not ignored.
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        scanner = Scanner(module, package, modules)
        scanner.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        edges.extend(scanner.edges)
    return modules, tuple(edges)


def violations(modules: set[str], edges: tuple[Edge, ...]) -> tuple[Violation, ...]:
    result: list[Violation] = []
    graph: dict[str, set[str]] = {m: set() for m in modules}
    for edge in edges:
        if not edge.type_only and edge.target in modules:
            graph[edge.source].add(edge.target)
    # Python executes package initializers too. These implicit edges participate
    # in reachability without inventing source import statements/exemptions.
    for module in modules:
        parent = module.rpartition(".")[0]
        if parent in modules:
            graph[module].add(parent)

    def reaches(start: str, goal: str) -> bool:
        pending, seen = [start], set()
        while pending:
            node = pending.pop()
            if node == goal:
                return True
            if node not in seen:
                seen.add(node)
                pending.extend(sorted(graph[node] - seen))
        return False

    for edge in edges:
        owner = domain(edge.source)
        if edge.target == "<dynamic>":
            result.append(Violation("unresolved-dynamic-import", edge))
        elif edge.target == PACKAGE or edge.target.startswith(PACKAGE + "."):
            if edge.target not in modules:
                result.append(Violation("unresolved-local-import", edge))
                continue
            if domain(edge.target) not in ALLOWED[owner]:
                result.append(Violation("domain-direction", edge))
            if not edge.type_only and reaches(edge.target, edge.source):
                result.append(Violation("runtime-cycle-edge", edge))
        else:
            external = edge.target.split(".")[0]
            if (
                external in INFRA_ROOTS or edge.target.startswith(("urllib.request", "http.client"))
            ) and owner not in {Domain.INFRA, Domain.APPLICATION}:
                result.append(Violation("infrastructure-in-domain", edge))
            elif (
                owner not in {Domain.INFRA, Domain.APPLICATION}
                and external not in sys.stdlib_module_names
                and external != "__future__"
                and not (
                    owner is Domain.RESEARCH
                    and external in {"numpy", "sklearn", "scipy", "threadpoolctl", "joblib"}
                )
            ):
                result.append(Violation("external-in-domain", edge))
    return tuple(result)


def compare(current: tuple[Violation, ...], baseline: list[dict[str, Any]]) -> list[str]:
    expected: Counter[str] = Counter()
    for item in baseline:
        if (
            set(item) != {"key", "count", "reason"}
            or not isinstance(item["key"], str)
            or type(item["count"]) is not int
            or item["count"] < 1
            or not isinstance(item["reason"], str)
            or not item["reason"].strip()
        ):
            raise ValueError("Invalid architecture debt entry")
        if item["key"] in expected:
            raise ValueError("Duplicate architecture debt key")
        expected[item["key"]] = item["count"]
    actual = Counter(v.key for v in current)
    labels = {v.key: v.describe() for v in current}
    failures = [
        f"NEW violation ({count} occurrence(s)): {labels[key]}"
        for key, count in sorted((actual - expected).items())
    ]
    failures.extend(
        f"STALE debt exception ({count} occurrence(s)); remove/update explicitly: {key}"
        for key, count in sorted((expected - actual).items())
    )
    return failures
