# AIGC Project Team

This repository is led by a four-agent team defined in `team/agents.json` and
`team/panes.json`. The team always consists of two Codex agents and two OMP
agents.

## Roles

| Agent | Runtime | Responsibility | Default write scope |
| --- | --- | --- | --- |
| `aigc-lead-codex` | Codex | Product direction, architecture, task decomposition, integration, and final decisions | Root configuration, `docs/architecture/`, and approved cross-boundary changes |
| `aigc-build-codex` | Codex | Feature implementation, tests, and technical documentation | `src/`, `tests/`, and implementation-owned documentation |
| `aigc-product-omp` | OMP | Requirements, research, UX flows, acceptance criteria, and product documentation | `docs/product/` and `research/` |
| `aigc-quality-omp` | OMP | Independent review of correctness, security, accessibility, and release evidence | `docs/quality/`; source code is read-only unless the lead assigns a fix |

## Working Rules

1. Read this file and the task-relevant repository instructions before work.
2. The lead decomposes work and owns shared contracts, dependencies, repository
   structure, CI, and integration decisions.
3. Agents stay within their default write scope unless the lead explicitly
   grants a narrower cross-boundary task.
4. Product research is decision input, not production truth. Cite sources and
   label assumptions, geography, date, and confidence.
5. Quality review remains independent. Findings include the affected file or
   behavior, impact, reproduction evidence, and required verification.
6. Never place credentials, tokens, cookies, `.env` values, customer data, or
   raw agent session dumps in prompts, notes, commits, or the knowledge vault.
7. A delivery is complete only after the owner reports changed scope, test
   evidence, assumptions, and unresolved risks, and the lead accepts it.

## External Knowledge Base

Use the Obsidian vault at `D:\aiworker\knowledge_brain` as a read-first external
knowledge base. Before implementation, search it with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\aiworker\knowledge_brain\codex\scripts\search.ps1 -Query "<topic>"
```

Verify all memory claims against the current repository. Repository code and
local instructions outrank memory notes. Treat `inferred` and `unverified`
notes as leads. Durable lessons belong under the vault's `codex/` Markdown
layer with source path, date, confidence, and unresolved risks. Do not modify
`knowledge_brain/raw/` or bypass the governed Raw -> Review -> Wiki gates.

## Shared Herdr Coordination

- Use scripts/herdr-control.ps1 from the AIGC root for task identity, bounded progress, checkpoints, watchdog diagnostics, session audits, and dispatch validation. Task create/transition/archive also mirrors `.agents/coordination/tasks.json` into the anti-loop ledger.
- The shared guard rejects workspace drift, placeholder wait IDs, missing collaboration targets, stale progress, and repeated schema failures.
- Rotate a lead context at 1,500 events or 170,000 estimated tokens; two tool_schema_* errors require a checkpoint and clean-context takeover. Supervisor early handoff is 900 events or 110,000 estimated tokens and does not reset anti-loop budgets.
- The AIGC anti-loop guard and its local runtime state remain authoritative for action budgets, retries, and dependency cycles; HERDR/HACP is delivery and display only. Live sessions start one `TEAM-ROOT` run owned by `aigc-lead-codex`. Lead admits every dispatch/resume through `python -m harness.anti_loop`; workers take one child run each. Coordination `progress` notes are not anti-loop progress.
- Never place secrets, raw session content, customer data, or provider payloads in coordination state.
