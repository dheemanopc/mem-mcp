#!/usr/bin/env python3
"""Materialize the PMO knowledge base from its memsys export into a file tree.

Source of truth is `_source/memories.json` — a verbatim export of every
`current`-tagged memory in the memsys `pmo` team (decisions, facts, notes),
each with full content + metadata. This script is deterministic: delete the
generated dirs and re-run to rebuild identical output, or re-export
`_source/memories.json` from memsys and re-run to refresh.

Layout produced (relative to the pmo/ repo root):

  prompts/       operator-facing kickoff prompts (orchestrator init + megaprompt pointer)
  roles/         the six role-prompts (PM, PA, DM, DA, Developer, Reviewer)
  config/        runtime config the engine dispatches on (permission matrix,
                 role-configs, directive grammar, named-query resolvers,
                 session registry, vocabulary convention, directive-emission patch)
  architecture/  durable design: project manifest, Area E substrate, stop-hook
  backlog/       v2 workstreams (not yet built)
  examples/      demo-seed content (Agent Edge Dating, login-screen test project,
                 work-item spine + edges) — illustrative, not part of the engine
  archive/build-log/   the PMO v1 build process log (DO/DA/PM memos, escalations,
                 ratifications, per-task verifications) — provenance, not prompts

Every file carries a YAML front-matter header recording its memsys provenance.
INDEX.md (a full catalog) and per-section READMEs are also generated.

Usage:  python tools/build_pmo_repo.py
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "_source" / "memories.json"
EXTRACTED_ON = date.today().isoformat()

# Generated section directories (wiped + rebuilt each run).
SECTIONS = [
    "prompts",
    "roles",
    "config",
    "architecture",
    "backlog",
]

# Curated placement + clean filename for the durable knowledge-base artifacts.
# Everything not listed here is routed by tag rules (see classify()).
OVERRIDES: dict[str, tuple[str, str]] = {
    # Kickoff prompts
    "19f45258-d615-44d7-b3e3-b11648a787b0": ("prompts", "orchestrator-init"),
    "8702533e-9546-4e7f-8891-7ccf8994a913": ("prompts", "orchestrator-megaprompt-pointer"),
    # Six role-prompts (v3)
    "127793ef-09a9-45d7-93da-a149165292ab": ("roles", "pm"),
    "897c696d-2d62-4c6a-8064-8327cf07cfbc": ("roles", "pa"),
    "99c6c6cd-449e-44ed-8981-51b489c43ade": ("roles", "dm"),
    "4e29970b-1ec9-4304-b648-c71057fac6d3": ("roles", "da"),
    "da1c4c82-6c93-4eb0-bd48-b1bb482fedf6": ("roles", "developer"),
    "6fff0238-852c-4206-b41b-87a37206ac96": ("roles", "reviewer"),
    # Engine config
    "238b450b-a7da-40ed-8cbc-d6e3b0000224": ("config", "permission-matrix-and-role-configs"),
    "42022af0-ac96-44bf-ad42-33dfa6b33a9e": ("config", "named-query-resolvers"),
    "ab0e7126-d0c6-481f-bfda-6f6b85fb0bd8": ("config", "directive-grammar-v2"),
    "c4579996-4681-4143-9fdd-0e277fffe08c": ("config", "directive-grammar-shortform"),
    "03e83396-500e-4131-81a2-cfbb80a2ba4c": ("config", "directive-emission-patch"),
    "5ae2759b-795d-41b9-b730-6b1eeeabb83f": ("config", "session-registry"),
    "940cfbae-e4bf-482f-8380-f39bb859d74c": ("config", "thin-vocabulary-convention"),
    # Architecture / substrate
    "75e8523c-2bdf-411d-bd96-bbc41ce8b931": ("architecture", "project-manifest"),
    "3c5900e5-b7c3-4587-a0d0-cfe6fc03afc3": ("architecture", "area-e-substrate-plan"),
    "714c71a6-0e0b-4658-a1c0-4dfeda6d1ffa": ("architecture", "area-e-routing-config"),
    "b194ba79-1602-449f-b06a-398220d31ca1": ("architecture", "substrate-stop-hook"),
    # v2 backlog
    "ea4aff7e-4dcb-4de4-9947-cadf77cede0c": ("backlog", "v2-project-setup-generator"),
    "9d1a6a18-9fd1-4bd6-8af5-4980c153dcdf": ("backlog", "v2-token-optimization"),
}

# Tag sets that route the remaining (uncurated) memories.
EXAMPLE_TAGS = {"demo-seed", "pmo-seed", "pmo-spine-edge", "pmo-project-testing"}

SECTION_BLURB = {
    "prompts": "Operator-facing kickoff prompts — paste these into a fresh session.",
    "roles": "The six PMO role-prompts (templates for the generic engine).",
    "config": "Runtime configuration the orchestrator engine dispatches on.",
    "architecture": "Durable design decisions: manifest, substrate, routing.",
    "backlog": "v2 workstreams — captured, not yet built.",
}


def kebab(text: str, max_len: int = 60) -> str:
    text = text.strip().lstrip("#").strip()
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "untitled"


def title_of(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line:
            return line.lstrip("#").strip().strip("*").strip() or "(untitled)"
    return "(untitled)"


def classify(mem: dict) -> tuple[str, str]:
    """Return (section, basename-without-date-prefix) for a memory."""
    mid = mem["id"]
    if mid in OVERRIDES:
        return OVERRIDES[mid]
    return ("", "")  # not part of the durable knowledge base — skipped


def front_matter(mem: dict) -> str:
    tags = ", ".join(mem.get("tags", []))
    return (
        "---\n"
        "source: memsys (team: pmo)\n"
        f"id: {mem['id']}\n"
        f"type: {mem['type']}\n"
        f"version: {mem.get('version', '')}\n"
        f"is_current: {mem.get('is_current', '')}\n"
        f"created_at: {mem.get('created_at', '')}\n"
        f"updated_at: {mem.get('updated_at', '')}\n"
        f"tags: [{tags}]\n"
        f"extracted_at: {EXTRACTED_ON}\n"
        "---\n\n"
    )


def main() -> int:
    memories = json.loads(SOURCE.read_text())
    # Stable order: by created_at then id.
    memories.sort(key=lambda m: (m.get("created_at", ""), m["id"]))

    # Clean generated sections (keep _source, tools, top-level docs).
    for section in SECTIONS:
        d = ROOT / section
        if d.exists():
            for f in d.glob("*.md"):
                f.unlink()

    used: set[Path] = set()
    catalog: list[dict] = []

    for mem in memories:
        section, base = classify(mem)
        if not section:
            continue
        secdir = ROOT / section
        secdir.mkdir(parents=True, exist_ok=True)

        # Build-log files get a date prefix so the log reads chronologically.
        if section == "archive/build-log":
            day = (mem.get("created_at") or "")[:10] or "0000-00-00"
            base = f"{day}-{base}"

        path = secdir / f"{base}.md"
        if path in used:  # disambiguate collisions with a short id suffix
            path = secdir / f"{base}-{mem['id'][:8]}.md"
        used.add(path)

        path.write_text(front_matter(mem) + mem["content"].rstrip() + "\n")
        catalog.append(
            {
                "section": section,
                "file": path.relative_to(ROOT).as_posix(),
                "id": mem["id"],
                "type": mem["type"],
                "title": title_of(mem["content"]),
            }
        )

    _write_section_readmes()
    _write_index(catalog)
    print(f"Wrote {len(catalog)} memories across {len(SECTIONS)} sections.")
    return 0


def _write_section_readmes() -> None:
    for section, blurb in SECTION_BLURB.items():
        readme = ROOT / section / "README.md"
        files = sorted(p.name for p in (ROOT / section).glob("*.md") if p.name != "README.md")
        lines = [f"# {section}", "", blurb, ""]
        lines += [f"- [`{f}`](./{f})" for f in files]
        readme.write_text("\n".join(lines) + "\n")


def _write_index(catalog: list[dict]) -> None:
    by_section: dict[str, list[dict]] = {}
    for entry in catalog:
        by_section.setdefault(entry["section"], []).append(entry)

    out = [
        "# PMO knowledge base — index",
        "",
        f"{len(catalog)} memories exported from the memsys `pmo` team "
        f"(extracted {EXTRACTED_ON}). Generated by `tools/build_pmo_repo.py`.",
        "",
    ]
    for section in SECTIONS:
        entries = sorted(by_section.get(section, []), key=lambda e: e["file"])
        if not entries:
            continue
        out += [f"## {section}", "", "| file | type | id | title |", "|---|---|---|---|"]
        for e in entries:
            fname = e["file"].split("/")[-1]
            out.append(
                f"| [`{fname}`]({e['file']}) | {e['type']} | `{e['id'][:8]}` | {e['title']} |"
            )
        out.append("")
    (ROOT / "INDEX.md").write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
