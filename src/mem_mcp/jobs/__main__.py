"""Entrypoint so `python -m mem_mcp.jobs` works."""

from mem_mcp.jobs._runner import main

if __name__ == "__main__":
    raise SystemExit(main())
