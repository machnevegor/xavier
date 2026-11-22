# AGENTS.md

Rules for AI agents working in this repository. Apply them to every task.

## Rule strength

- **MUST / MUST NOT** — mandatory.
- **SHOULD / SHOULD NOT** — default; deviate only for a concrete technical
  reason.
- **MAY** — optional.

Higher-priority user or system instructions override this file.

## Operating principles

- **MUST** prioritize correctness over agreement. Push back on incorrect
  assumptions.
- **MUST** surface material contradictions before writing code.
- **MUST** ask before making a decision that materially changes public behavior,
  compatibility, security, data integrity, or task scope when the repository
  does not determine the answer.
- **SHOULD** resolve minor ambiguity from repository conventions instead of
  asking unnecessary questions.
- **MUST** respond in the language used by the user.
- **MUST NOT** add filler, performative agreement, or emojis unless requested.

## Understand before changing

- **MUST** inspect the relevant current code, configuration, and tests before
  editing.
- **MUST** treat the repository as the primary source of truth.
- **MUST** verify version-sensitive external facts against official
  documentation when they affect the implementation.
- **SHOULD** search broadly first, then read only the files needed for the task.
- **MUST** cite repository references as `path/to/file.py:line`.
- **MUST NOT** rely on remembered repository state or stale library APIs.

For non-trivial work, define a short plan and concrete success criteria before
implementation. Trivial changes do not require ceremony.

## Implementation

- **MUST** implement the smallest clear solution that fully satisfies the
  requested behavior.
- **MUST NOT** add speculative features, configurability, abstractions, or
  fallback paths.
- **MUST NOT** introduce an abstraction for a single use unless it materially
  improves clarity or enforces an important boundary.
- **MUST** prefer explicit, typed contracts at public, service, and integration
  boundaries.
- **MUST** fail clearly rather than hide real errors behind silent fallbacks.
- **MUST NOT** add backward-compatibility shims unless explicitly required.
- **MUST** preserve existing behavior outside the requested change.
- **SHOULD** edit existing files instead of creating new ones when the existing
  structure remains appropriate.

### Replace complexity; do not layer it

When task-relevant code is already unnecessarily complex and adding another
patch would deepen that complexity:

- **MUST** prefer a coherent rewrite of the bounded unit over another
  incremental layer.
- A bounded unit is typically a function, class, or clearly isolated module
  section.
- **MUST** preserve all observable behavior outside the requested change.
- **MUST** retain or add regression coverage before relying on the rewrite.
- **MUST** remove obsolete branches, helpers, and indirection made unnecessary
  by the rewrite.
- **MUST NOT** use this rule to refactor unrelated neighboring code.

If 200 lines of task-relevant code can become 50 clear, correct, and
maintainable lines, rewrite the block rather than adding line 201.

Fewer lines are not the goal. Lower accidental complexity and clearer behavior
are.

## Repository layout

The repository holds two apps with separate toolchains:

- `apps/server/` — the Python backend (uv): REST API, Temporal worker and the
  `xavier` CLI.
  - `src/` — `api` (FastAPI), `orchestration` (Temporal workflows, activities,
    worker), `agent` (turn runner, MCP gateway), `tools`, `policy`, `observe`,
    `execute`, `compute`, `workspace`, `storage`, `db` (models, Alembic
    migrations).
  - `images/` — the sandbox, desktop and firewall images.
  - `tests/unit/` — unit tests; `tests/integration/` — tests against
    `docker-compose.test.yaml`, run with `-m integration`.
- `apps/client/` — the TypeScript frontend, the Web UI (pnpm, TanStack Start,
  shadcn/ui).
  - `src/routes/` — routes; `src/features/` — screens by domain;
    `src/components/` — shared components, `src/components/ui/` — shadcn/ui;
    `src/api/` — the typed API client.
  - `tests/` — Playwright end-to-end tests; Vitest tests live next to the code
    as `src/**/*.test.ts`.
  - `pnpm gen` writes the OpenAPI document and its types to `__generated__/`
    (not committed).
- `docker-compose.yaml` — the apps behind Traefik; `infra/docker-compose.yml` —
  PostgreSQL, Temporal and RustFS; `docker-compose.test.yaml` — the integration
  test infrastructure; `scripts/` — their init scripts and configs.
- `.devcontainer/` — the Dev Container for both apps.
- `docs/` — architecture, agent, workspace, Observe, tools, security, Web UI,
  operations, development, testing, performance.

## Python

This project targets Python 3.14+.

- **MUST NOT** add `from __future__ import annotations`; annotations are
  deferred by default.
- **MUST NOT** use defensive `try: import ... except ImportError` for declared
  dependencies.
- **MUST NOT** add fallbacks for states that cannot legitimately occur.
- **MUST NOT** use `typing.Any` except at unavoidable untyped or third-party
  boundaries.
- When `Any` is unavoidable, **MUST** keep it narrow and add a local
  `# noqa: ANN401` with the reason.

## Documentation

Regular Python docstrings **MUST** follow NumPy docstring style. Do not mix
NumPy, Google, or Sphinx conventions.

- Simple modules, classes, methods, and functions **SHOULD** use a concise
  one-line docstring.
- `Parameters`, `Returns`, `Raises`, `Yields`, `Notes`, and similar sections
  **SHOULD NOT** be added unless they provide information needed to understand
  or safely use non-trivial code.
- Multi-section NumPy docstrings **SHOULD** be reserved for complex,
  business-critical, or service-critical APIs.

### FastAPI handlers

FastAPI handler docstrings are OpenAPI documentation, not regular Python API
documentation.

- **MUST** write them as concise Markdown for API consumers.
- **SHOULD** explain what the endpoint does, when or why it is used, and
  important consumer-visible constraints.
- **MUST NOT** document implementation details.
- **MUST NOT** add NumPy `Parameters`, `Returns`, `Raises`, `Yields`, or `Notes`
  sections merely for style compliance.
- **SHOULD** express parameters, schemas, responses, and status codes through
  FastAPI/OpenAPI mechanisms.

In short: regular Python code uses NumPy style; FastAPI handlers use
consumer-facing Markdown.

## Testing

- **MUST** test observable behavior and contracts, not implementation details.
- Behavior changes and bug fixes **MUST** have regression coverage unless an
  existing test demonstrably covers the contract.
- For reproducible bugs, **SHOULD** reproduce the failure with a test before
  fixing it.
- **SHOULD** mock at external system boundaries, not internal implementation
  details.
- Tests **MUST** be independent and parallel-safe.
- Unit tests **MUST NOT** access the network. Socket restrictions configured by
  the project must remain enabled.
- Async tests use `asyncio_mode = "auto"`; **MUST NOT** add redundant
  `@pytest.mark.asyncio`.

## Tooling and validation

Use the project toolchain, in `apps/server`:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

For changes under `apps/client/`, run in `apps/client/`:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

End-to-end tests in `apps/client/tests/` run with `pnpm test:e2e` against the
running stack.

Client dependency changes **MUST** go through `pnpm add` and `pnpm remove`.

During iteration, **SHOULD** run the narrowest relevant checks first.

Before completing a Python code change:

1. **MUST** run `uv run ruff check .`.
2. **MUST** run `uv run ruff format --check .`.
3. **MUST** run `uv run ty check`.
4. **MUST** run the relevant pytest tests.
5. **MUST** run the full `uv run pytest` suite for shared code, public
   contracts, cross-cutting changes, or when targeted tests do not sufficiently
   bound regression risk.

To apply formatting, **SHOULD** format only task-relevant paths with
`uv run ruff format <paths>`.

If a required check cannot run, **MUST** report exactly which check was skipped
and why. Do not claim the change is fully verified.

## Dependencies

Dependency changes **MUST** go through `uv`.

```bash
uv add <package>
uv add --group dev <package>
uv lock --upgrade-package <package>
uv remove <package>
```

- **MUST NOT** hand-edit dependency declarations in `pyproject.toml`.
- **MUST NOT** hand-edit `uv.lock`.
- **MUST NOT** choose version constraints manually unless the task requires a
  specific constraint.
- **MUST** inspect dependency and lockfile diffs after resolver operations.
- **MUST NOT** perform broad dependency refreshes or upgrades unless requested.

## Git and safety

- **MUST** keep the diff scoped to the task.
- **MUST** remove imports, variables, helpers, and files made obsolete by your
  own change.
- **MUST NOT** perform unrelated cleanup.
- **MUST NOT** stage or commit secrets such as `.env`, credentials, or
  `*.local`.
- **MUST NOT** run `git commit`, `git push`, force-push, or open a pull request
  unless explicitly requested.
- When asked to commit, **MUST** follow the repository's recent commit style;
  otherwise use `feat|fix|chore|refactor|test|docs|ci: subject`.
- **MUST NOT** run destructive operations such as `rm -rf`, `git reset --hard`,
  or broad destructive lockfile operations without explicit permission.
- **MUST NOT** create scratch Markdown files, progress reports, `README`,
  `QUICKSTART`, or `TASK` files unless requested.
- **MUST NOT** leave TODO comments as a substitute for completing requested
  work.

## Completion

A task is complete only when:

- the requested behavior is implemented;
- the final diff has been reviewed;
- required validation has passed or skipped checks are explicitly disclosed;
- no unrelated changes remain;
- no obsolete code introduced or exposed by the change remains.

Report what actually changed, the exact checks run, their results, and any
remaining unverified assumptions. Never report intended work as completed work.
