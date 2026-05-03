"""Maintenance jobs invoked by systemd timers via `python -m mem_mcp.jobs <name>`.

Each job module exposes ``async def main(dry_run: bool = False, ...) -> int``
returning the count of affected rows (or 0). Jobs use mem_maint role via
system_tx (BYPASSRLS).

Available jobs:
- cleanup_clients: DCR client cleanup (T-4.9)
- retention_memories: soft + hard-delete memories per retention policy (T-7.14)
- retention_tokens: purge expired link_state + web_sessions (T-7.14)
- retention_audit: anonymize + hard-delete audit log (T-7.15)
- retention_deletion: finalize pending tenant deletions (T-7.14)
"""
