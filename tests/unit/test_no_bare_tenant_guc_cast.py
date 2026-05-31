"""Guard against re-introducing bare `current_setting(...)::uuid` in migrations.

The systemic defect documented in memsys:407fbf20 and memsys:96464537:
PostgreSQL custom GUCs reset to '' (not NULL) after SET LOCAL + ROLLBACK,
so any bare cast `current_setting('app.current_tenant_id', true)::uuid`
crashes on recycled pool connections.

Migration 0034 introduced `safe_current_tenant_id()` and swapped every
known call site. This test enforces: new migrations cannot reintroduce
the bare cast — they must use the helper.

Allowlist: the historical migrations that PRE-DATE 0034 are exempt
because their bare casts are superseded at runtime by 0034. The
allowlist exists so this guard remains effective on new code without
requiring us to rewrite history.
"""

from __future__ import annotations

import re
from pathlib import Path

ALEMBIC_DIR = Path(__file__).parent.parent.parent / "alembic" / "versions"

# Migrations that contain bare casts in their source but whose effective
# runtime state is overridden by migration 0034 (or by an earlier NULLIF
# migration). These are historical — they were the bug. The fix lives in
# 0034 and later. New migrations must NOT match this allowlist.
ALLOWLISTED_HISTORICAL = frozenset(
    {
        # original schema + first wave of per-tenant tables
        "0001_initial_schema.py",
        "0004_2026_05_17_add_skill_credentials.py",
        "0007_2026_05_18_kite_intents.py",
        "0010_2026_05_19_memory_clusters.py",
        # enterprise + RBAC scaffolding — many bare casts, mostly superseded
        "0015_2026_05_24_enterprise_phase1a.py",
        "0016_2026_05_25_rls_security_definer_functions.py",
        # teams_select bare-cast introduced; superseded by 0034
        "0027_2026_05_30_teams_select_creator.py",
        # bare casts kept as downgrade body, helper applied on upgrade
        "0019_2026_05_28_rls_funcs_nullif_empty_tenant.py",
        # async_write_queue first three patches — superseded by 0031 + 0034
        "0028_2026_05_31_async_write_queue.py",
        "0029_2026_05_31_async_write_queue_select_drain_fix.py",
        "0030_2026_05_31_aq_policies_null_guc.py",
        "0031_2026_05_31_aq_policies_nullif.py",
        # memories_select + helper fns NULLIF migration — downgrade restores bare cast
        "0033_2026_05_31_nullif_memories_rls.py",
        # this migration itself — downgrade re-creates the bare-cast policies
        "0034_2026_05_31_safe_current_tenant_id.py",
    }
)

# Pattern: `current_setting(...app.current_tenant_id...)::uuid` NOT preceded
# by NULLIF on the same line. Approximate but catches the bug shape.
BARE_CAST_RE = re.compile(
    r"current_setting\([^)]*app\.current_tenant_id[^)]*\)\s*::\s*uuid",
    re.IGNORECASE,
)
NULLIF_GUARD_RE = re.compile(
    r"NULLIF\([^)]*current_setting\([^)]*app\.current_tenant_id",
    re.IGNORECASE,
)


def test_new_migrations_use_safe_helper() -> None:
    """Any migration not on the historical allowlist must not bare-cast the GUC.

    The fix is to use `safe_current_tenant_id()` (defined in migration 0034)
    or to wrap with NULLIF(..., '')::uuid manually.
    """
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(ALEMBIC_DIR.glob("*.py")):
        if path.name in ALLOWLISTED_HISTORICAL:
            continue
        if path.name == "__init__.py":
            continue
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if BARE_CAST_RE.search(line) and not NULLIF_GUARD_RE.search(line):
                offenders.append((path.name, lineno, line.strip()))

    assert not offenders, (
        "New migrations re-introduce bare `current_setting(...)::uuid`. "
        "Use safe_current_tenant_id() (migration 0034) or wrap with NULLIF. "
        f"Offenders: {offenders}"
    )
