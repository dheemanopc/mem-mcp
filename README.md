# pmo-bootstrap

**Bootstrap kit for the PMO** — the prompts, role definitions, and engine
config that stand up a Project Management Office on top of memsys.

The PMO is a multi-role project-orchestration layer that runs **on top of
[memsys](https://memsys.dheemantech.in) (the Personal Memory MCP)**. A single
"one-window" orchestrator session, given just a project name, scaffolds the
project in memsys and spawns role agents (PM, PA, DM, DA, Developer, Reviewer)
that coordinate entirely through memory reads/writes and a small directive
grammar.

This repository is the canonical, file-based home of the PMO **knowledge
base** — the prompts, configs, and design decisions — extracted from the
memsys `pmo` team so they live in version control: reviewable and diffable.

> **Provenance.** Every file was generated from `_source/memories.json`, a
> verbatim export of the durable `current`-tagged memories in the memsys `pmo`
> team. Each file's YAML front-matter records its memsys `id`, `type`,
> `version`, `tags`, and timestamps. memsys remains the live source of truth;
> this repo is a snapshot — see "Refreshing" below.
>
> This repo carries the **durable knowledge base** only. The PMO v1 build
> process log and demo-seed examples are intentionally excluded here (they
> remain in memsys).

## Layout

| Directory | What's in it |
|---|---|
| [`prompts/`](./prompts) | Operator-facing kickoff prompts — the orchestrator init prompt + the megaprompt canonical pointer. **Start here.** |
| [`roles/`](./roles) | The six role-prompts (v3): PM, PA, DM, DA, Developer, Reviewer. |
| [`config/`](./config) | Runtime config the engine dispatches on: permission matrix + role-configs, directive grammar, named-query resolvers, session registry, thin-vocabulary convention, directive-emission patch. |
| [`architecture/`](./architecture) | Durable design: project manifest, Area E (tmux one-window substrate), routing config, substrate stop-hook. |
| [`backlog/`](./backlog) | v2 workstreams captured but not yet built. |

A full catalog (id, type, title, file) is in [`INDEX.md`](./INDEX.md).

## Using the prompts

1. Open a fresh Claude Code session that has **both** the memsys MCP tools and
   bash.
2. Paste [`prompts/orchestrator-init.md`](./prompts/orchestrator-init.md) and
   give it a project name. It scaffolds the project in memsys and spawns the
   role agents, each of which self-registers via the SessionStart hook.
3. The role agents load their prompt from [`roles/`](./roles) and coordinate
   via memsys using the grammar in
   [`config/directive-grammar-v2.md`](./config/directive-grammar-v2.md).

## Refreshing from memsys

memsys is authoritative. To refresh this mirror after the PMO memories change:

1. Re-export the durable `current`-tagged memories in the `pmo` team to
   `_source/memories.json` (an array of memory objects with `content` +
   metadata — the shape returned by `memory_get_batch`).
2. Regenerate the tree:

   ```bash
   python tools/build_pmo_repo.py
   ```

The generator is deterministic — the `prompts/`, `roles/`, `config/`,
`architecture/`, and `backlog/` directories are wiped and rebuilt from the
export each run.

## License

Apache-2.0 — see [`LICENSE`](./LICENSE).
