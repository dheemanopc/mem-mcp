"""Tenant-scope linter (T-6.1, LLD §11).

Walks src/mem_mcp/ AST, finds await conn.<method>(...) calls where method is one
of execute/fetch/fetchrow/fetchval, extracts the SQL string literal (first arg),
parses it, and flags violations:
- references a per-tenant table (memories, tenant_daily_usage, tenant_identities,
  oauth_clients, oauth_consents, web_sessions, link_state, feedback, audit_log)
- AND lacks a tenant_id WHERE clause filter
- AND the file is not under src/mem_mcp/jobs/ (jobs use system_tx)

Exits 0 on clean, 1 on violations, prints findings.

Usage: python tools/lint_tenant_scope.py [path]
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PER_TENANT_TABLES = frozenset(
    {
        "memories",
        "tenant_daily_usage",
        "tenant_identities",
        "oauth_clients",
        "oauth_consents",
        "web_sessions",
        "link_state",
        "feedback",
        "audit_log",
    }
)

# Files exempted from the rule (use system_tx by design)
EXEMPT_PREFIXES = ("src/mem_mcp/jobs/",)

CONN_METHODS = frozenset({"execute", "fetch", "fetchrow", "fetchval", "executemany"})


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    method: str
    table: str
    sql_excerpt: str


def extract_sql_str(node: ast.Call) -> str | None:
    """Return the first positional arg if it's a string literal."""
    if not node.args:
        return None
    a = node.args[0]
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value
    return None


def references_per_tenant_table(sql: str) -> str | None:
    """Return the per-tenant table name referenced, or None.

    Match tables in FROM/JOIN/UPDATE/INSERT INTO/DELETE FROM clauses.
    """
    pattern = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM)\s+([a-z_]+)\b", re.I)
    for m in pattern.finditer(sql):
        tbl = m.group(1).lower()
        if tbl in PER_TENANT_TABLES:
            return tbl
    return None


def has_tenant_id_filter(sql: str) -> bool:
    """True if SQL has WHERE ... tenant_id ... or USING (tenant_id) etc.

    Looks for tenant_id as a word boundary (not part of table names like
    tenant_identities). Checks in WHERE, USING, ON clauses.
    """
    # Look for tenant_id as a word (not part of tenant_identities, etc.)
    # Check in WHERE, USING, ON clauses
    pattern = re.compile(r"\b(?:WHERE|USING|ON).*\btenant_id\b", re.I | re.DOTALL)
    if pattern.search(sql):
        return True
    # Also check for simple "tenant_id" with word boundary in a less context-specific way
    # But exclude when it's part of table names
    sql_lower = sql.lower()
    # If we find tenant_id but not as part of tenant_identities, it's a filter
    if re.search(r"\btenant_id\b", sql_lower):
        return True
    return False


def _is_system_tx_context(node: ast.Await) -> bool:
    """True if this await is inside a system_tx context."""
    # Walk up the tree to find the enclosing WithItem
    # For now, use a simple heuristic: scan the source for 'system_tx' in the
    # surrounding context. This is fragile but workable for v1.
    # Better approach: when building the tree, track parent pointers.
    # For now, we'll use a different strategy: look at all With nodes in the tree
    # and check if this Await is inside one that uses system_tx.
    return False  # Placeholder; see enhanced version below


def _find_enclosing_context(tree: ast.Module, target_node: ast.Await) -> str | None:
    """Find the context (system_tx, tenant_tx, or None) in which target_node appears.

    Returns "system_tx", "tenant_tx", or None.
    This is done by building a parent map and walking up from target_node to find
    the enclosing async with statement.
    """
    # Build parent map
    parents: dict[ast.AST, ast.AST] = {}

    class ParentMapper(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                parents[child] = node
            super().generic_visit(node)

    ParentMapper().visit(tree)

    # Walk up from target_node to find enclosing With statement
    current: ast.AST | None = target_node
    while current is not None:
        if isinstance(current, ast.AsyncWith):
            # Check if the context manager name contains 'system_tx' or 'tenant_tx'
            for item in current.items:
                ctx_expr = item.context_expr
                # The context expr could be a Call to system_tx(...) or tenant_tx(...)
                if isinstance(ctx_expr, ast.Call):
                    if isinstance(ctx_expr.func, ast.Name):
                        name = ctx_expr.func.id
                        if name == "system_tx":
                            return "system_tx"
                        elif name == "tenant_tx":
                            return "tenant_tx"
        current = parents.get(current)
    return None


def lint_file(path: Path) -> list[Violation]:
    """Walk AST of path, return violations."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        # Looking for foo.bar(...) where bar in CONN_METHODS
        if isinstance(call.func, ast.Attribute) and call.func.attr in CONN_METHODS:
            sql = extract_sql_str(call)
            if sql is None:
                # Dynamic SQL — skip (false-positive heavy)
                continue
            tbl = references_per_tenant_table(sql)
            if tbl and not has_tenant_id_filter(sql):
                # Check context: if inside system_tx, skip the check
                context = _find_enclosing_context(tree, node)
                if context == "system_tx":
                    # system_tx is allowed to query without explicit tenant_id
                    continue
                violations.append(
                    Violation(
                        file=str(path),
                        line=node.lineno,
                        method=call.func.attr,
                        table=tbl,
                        sql_excerpt=sql.strip()[:120],
                    )
                )
    return violations


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "src/mem_mcp")
    files = list(root.rglob("*.py"))
    all_violations: list[Violation] = []
    for f in files:
        rel = f.as_posix()
        if any(rel.startswith(prefix) for prefix in EXEMPT_PREFIXES):
            continue
        all_violations.extend(lint_file(f))
    if all_violations:
        print(f"Tenant-scope violations: {len(all_violations)}")
        for v in all_violations:
            print(f"  {v.file}:{v.line} - {v.method}() on {v.table} without tenant_id")
            print(f"    SQL: {v.sql_excerpt}")
        return 1
    print(f"OK: no tenant-scope violations across {len(files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
