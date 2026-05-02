"""Maintenance jobs invoked by systemd timers via `python -m mem_mcp.jobs <name>`.

Each job module exposes ``async def main(dry_run: bool = False, ...) -> int``
returning the count of affected rows (or 0). Jobs use mem_maint role via
system_tx (BYPASSRLS).
"""
