"""Dump JSON Schemas for every Pydantic model in mem_mcp.

Per T-X.5. Discovers models via the LLD §12 registry list. Writes one
JSON file per model into docs/schemas/. Used in CI to detect contract drift.

Usage:
    python tools/dump_schemas.py                   # writes to docs/schemas/
    python tools/dump_schemas.py --check           # exits 1 if any file would change
    python tools/dump_schemas.py --output PATH     # write to alternate dir
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

# Per LLD §12, the master list of Pydantic models. Module path → list of model names.
MODELS: list[tuple[str, str]] = [
    ("mem_mcp.config", "Settings"),
    ("mem_mcp.auth.jwt_validator", "JwtClaims"),
    ("mem_mcp.auth.middleware", "TenantContext"),
    ("mem_mcp.auth.dcr", "DcrInput"),
    ("mem_mcp.auth.dcr", "DcrOutput"),
    ("mem_mcp.embeddings.bedrock", "EmbedResult"),
    ("mem_mcp.mcp.tools.write", "MemoryWriteInput"),
    ("mem_mcp.mcp.tools.write", "MemoryWriteOutput"),
    ("mem_mcp.mcp.tools.search", "MemorySearchInput"),
    ("mem_mcp.mcp.tools.search", "MemorySearchOutput"),
    ("mem_mcp.mcp.tools.get", "MemoryGetInput"),
    ("mem_mcp.mcp.tools.get", "MemoryGetOutput"),
    ("mem_mcp.mcp.tools.list", "MemoryListInput"),
    ("mem_mcp.mcp.tools.list", "MemoryListOutput"),
    ("mem_mcp.mcp.tools.update", "MemoryUpdateInput"),
    ("mem_mcp.mcp.tools.update", "MemoryUpdateOutput"),
    ("mem_mcp.mcp.tools.delete", "MemoryDeleteInput"),
    ("mem_mcp.mcp.tools.delete", "MemoryDeleteOutput"),
    ("mem_mcp.mcp.tools.undelete", "MemoryUndeleteInput"),
    ("mem_mcp.mcp.tools.undelete", "MemoryUndeleteOutput"),
    ("mem_mcp.mcp.tools.supersede", "MemorySupersedeInput"),
    ("mem_mcp.mcp.tools.supersede", "MemorySupersedeOutput"),
    ("mem_mcp.mcp.tools.stats", "MemoryStatsOutput"),
    ("mem_mcp.mcp.tools.feedback", "MemoryFeedbackInput"),
    ("mem_mcp.mcp.tools.feedback", "MemoryFeedbackOutput"),
    ("mem_mcp.mcp.tools.export", "MemoryExportInput"),
    ("mem_mcp.mcp.tools.export", "MemoryExportOutput"),
    ("mem_mcp.quotas.tiers", "TierLimits"),
]


def _load_model(module_path: str, name: str) -> Any:
    """Import the model class. Returns None if not found (some are skipped)."""
    try:
        mod = import_module(module_path)
        return getattr(mod, name, None)
    except (ImportError, AttributeError):
        return None


def dump_schemas(out_dir: Path) -> dict[str, str]:
    """Write all schemas. Returns dict of {model_name: serialized_json}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for module_path, name in MODELS:
        cls = _load_model(module_path, name)
        if cls is None:
            print(f"WARN: model not found: {module_path}.{name}", file=sys.stderr)
            continue
        if not hasattr(cls, "model_json_schema"):
            print(f"WARN: not a Pydantic model: {module_path}.{name}", file=sys.stderr)
            continue
        schema = cls.model_json_schema()
        body = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        path = out_dir / f"{name}.json"
        path.write_text(body, encoding="utf-8")
        written[name] = body
    return written


def check_no_drift(out_dir: Path) -> int:
    """Return 0 if committed schemas match generated; 1 otherwise."""
    expected = dump_schemas(Path("/tmp/_schemas_check"))
    drift: list[str] = []
    for name, body in expected.items():
        path = out_dir / f"{name}.json"
        if not path.exists():
            drift.append(f"missing: {name}.json")
            continue
        if path.read_text(encoding="utf-8") != body:
            drift.append(f"changed: {name}.json")
    if drift:
        print("Schema drift detected:")
        for d in drift:
            print(f"  - {d}")
        print("Run `python tools/dump_schemas.py` and commit the updated schemas.")
        return 1
    print(f"OK: {len(expected)} schemas match committed versions.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dump_schemas")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if generated schemas differ from committed",
    )
    parser.add_argument("--output", default="docs/schemas", help="Output directory")
    args = parser.parse_args(argv)
    out = Path(args.output)
    if args.check:
        return check_no_drift(out)
    written = dump_schemas(out)
    print(f"Wrote {len(written)} schemas to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
