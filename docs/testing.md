# Testing

Xavier has five kinds of checks. Static checks and unit tests run anywhere
without services; integration and end-to-end tests need running infrastructure.

| Check                    | Where         | Command                                                                  | Needs                                                                                 |
| ------------------------ | ------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| Lint, format, types      | `apps/server` | `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` | uv                                                                                    |
| Server unit tests        | `apps/server` | `uv run pytest`                                                          | uv                                                                                    |
| Server integration tests | `apps/server` | `uv run pytest -m integration tests/integration`                         | Docker, the test stack, the sandbox and desktop images, the firewall, internet access |
| Lint, format, types      | `apps/client` | `pnpm lint`, `pnpm typecheck`                                            | Node.js, pnpm; uv for `typecheck`                                                     |
| Client unit tests        | `apps/client` | `pnpm test`                                                              | Node.js, pnpm                                                                         |
| Production build         | `apps/client` | `pnpm build`                                                             | Node.js, pnpm                                                                         |
| End-to-end tests         | `apps/client` | `pnpm test:e2e`                                                          | the running Compose stack, Playwright's Chromium                                      |

Install the dependencies first: `uv sync` in `apps/server` and `pnpm i` in
`apps/client`. The [Dev Container](development.md#dev-container) has both
toolchains.

## Server

### Static checks

```bash
cd apps/server
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Ruff runs with every rule enabled except a few listed in `pyproject.toml`, and
NumPy docstring conventions. Format changed files with
`uv run ruff format <paths>`.

### Unit tests

```bash
cd apps/server
uv run pytest
uv run pytest tests/unit/test_policy.py -k redirect   # a subset
```

`pyproject.toml` configures pytest so that a plain `uv run pytest`:

- runs `tests/` without the `integration` marker, which leaves the unit tests in
  `tests/unit/`;
- blocks network sockets (`pytest-socket`), allowing only Unix sockets, so a
  unit test that reaches the network fails;
- runs async tests in `asyncio_mode = "auto"`, without `@pytest.mark.asyncio`.

Unit tests replace external systems with fakes at their boundary: Temporal
workflows run in the Temporal test environment, Harness events come from a
recorded session (`tests/fixtures/harness/`), Docker and S3 are faked.

### Integration tests

Integration tests run against real PostgreSQL, Temporal, RustFS and Docker. They
cover the acceptance criteria of the platform:

| File                        | Covers                                                                                                                                             |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_database.py`          | migrations, including concurrent runs                                                                                                              |
| `test_storage.py`           | buckets and blobs in S3                                                                                                                            |
| `test_temporal.py`          | the namespace connection and large payloads in S3                                                                                                  |
| `test_agent_store.py`       | migrations against the models, decision races, fencing, the event cursor                                                                           |
| `test_chat_workflow.py`     | `ChatWorkflow` with a real worker and fake turns: approvals, pause, stop, limits, workspaces, parallel chats, two workers, a killed worker, replay |
| `test_workspace_execute.py` | code runs, limits, isolation, network, checkpoints, host loss, the Computer, the GUI lease, the warm pool                                          |
| `test_observe_polling.py`   | versions, child resources, limits, drafts, credentials, webhooks, browser mode                                                                     |
| `test_acceptance.py`        | schedules, a killed worker, turn fencing, prompt injection, outages of PostgreSQL, S3 and Temporal                                                 |
| `test_rest_api.py`          | the REST API against the real stores, and trace propagation into workflows                                                                         |

Acceptance criteria and their tests:

| Criterion                                             | Test                                                                                                                                                                                |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AT-01 an unchanged API produces no versions or alerts | `test_observe_polling.py::test_a_version_is_stored_only_when_the_normalized_value_changes`                                                                                          |
| AT-02 a change in a child resource is detected        | `test_observe_polling.py::test_children_are_followed_once_and_missing_ones_become_gone`                                                                                             |
| AT-03 a JavaScript site is observed                   | `test_observe_polling.py::test_browser_mode_observes_pages_rendered_by_javascript`                                                                                                  |
| AT-04 agent drafts need publication                   | `test_observe_polling.py::test_a_draft_is_not_polled_until_published`                                                                                                               |
| AT-05 a file becomes an artifact                      | `test_workspace_execute.py::test_files_persist_between_runs_and_exports_become_artifacts`                                                                                           |
| AT-06 code cannot reach host files or secrets         | `test_workspace_execute.py::test_runs_see_no_host_files_secrets_or_privileges`                                                                                                      |
| AT-07 browser and terminal in one task                | `test_workspace_execute.py::test_browser_terminal_and_code_runs_share_the_workspace`                                                                                                |
| AT-08 a screenshot reaches an image model             | `test_workspace_execute.py::test_screenshot_reaches_the_model_as_an_image`                                                                                                          |
| AT-09 a delayed task runs once                        | `test_acceptance.py::test_delayed_task_posts_once_into_a_new_chat`                                                                                                                  |
| AT-10 a schedule runs on its timetable                | `test_acceptance.py::test_schedule_posts_on_its_timetable_into_a_pinned_chat`                                                                                                       |
| AT-11 parallel chats                                  | `test_chat_workflow.py::test_parallel_chats_run_their_turns_at_once`                                                                                                                |
| AT-12 two tasks never share the GUI at once           | `test_workspace_execute.py::test_gui_calls_never_overlap_and_a_stale_token_is_rejected`                                                                                             |
| AT-13 a killed worker's turn is retried               | `test_chat_workflow.py::test_turn_of_a_killed_worker_is_retried_by_its_restarted_worker`, `test_acceptance.py::test_retry_after_a_killed_worker_settles_the_actions_of_its_attempt` |
| AT-14 a stale worker is fenced off                    | `test_acceptance.py::test_stale_worker_can_neither_take_nor_write_the_turn`                                                                                                         |
| AT-15 prompt injection meets a `deny` rule            | `test_acceptance.py::test_instruction_injected_into_a_page_cannot_make_a_denied_call`                                                                                               |
| AT-16 an expired credential pauses only its sources   | `test_observe_polling.py::test_a_rejected_credential_pauses_only_the_sources_using_it`                                                                                              |
| AT-17 turns spread over two workers                   | `test_chat_workflow.py::test_turns_of_many_chats_spread_over_two_workers`                                                                                                           |
| AT-18 a large artifact is passed by reference         | `test_workspace_execute.py::test_large_export_is_passed_by_reference`                                                                                                               |

#### Prerequisites

1. Docker with Compose, and access to the Docker socket.
2. The sandbox and desktop images:

   ```bash
   docker build -t xavier-sandbox:local apps/server/images/sandbox
   docker build -t xavier-desktop:local apps/server/images/desktop
   ```

3. The egress firewall of the sandbox bridge, installed from the repository
   root. Rules do not survive a restart of the host or Docker Desktop:

   ```bash
   docker compose run --rm firewall-init
   ```

4. Internet access: the Computer and browser-mode tests open public pages.
5. The test stack from `docker-compose.test.yaml`, a separate Compose project
   (`xavier-test`) with fixed credentials and in-memory storage. Its ports
   (PostgreSQL 35432, RustFS 19000, Temporal 17233) do not clash with the
   development infrastructure:

   ```bash
   docker compose -f docker-compose.test.yaml up -d --wait temporal rustfs
   docker compose -f docker-compose.test.yaml run --rm temporal-create-namespace
   ```

   `temporal-create-namespace` is a one-shot job, so it runs separately instead
   of being waited for.

#### Run

```bash
cd apps/server
uv run pytest -m integration tests/integration
uv run pytest -m integration tests/integration/test_observe_polling.py   # one file
uv run pytest -m integration tests/integration/test_interaction.py       # FakeLLM + Harness
```

The full suite takes about five minutes. The tests read their settings from
`tests/integration/conftest.py`, never from `settings.yaml` or `XAVIER_*`
variables, and use unique task queues and worker ids, so they can run while the
development stack is up. Run them on the host: the test stack publishes its
ports on the host's loopback interface only.

`tests/integration/fake_llm.py` is a scripted OpenAI-compatible streaming
server. Interaction tests point the Harness provider at it to exercise real
runtime turns (interruptible streaming, titles, assistant events) without a paid
model.

Tear the stack down afterwards; its data is in memory and disappears:

```bash
docker compose -f docker-compose.test.yaml down
```

### Workflow replay

`tests/unit/test_replay.py` replays every `ChatWorkflow` history stored in
`tests/fixtures/histories/chat_workflow/` against the current code, so a
non-deterministic change fails before it reaches running chats; such changes go
behind `workflow.patched`. No history is stored yet, so the test has no cases.
To record one from the integration test that drives a chat through approvals, a
pause, a stop and the deletion:

```bash
cd apps/server
XAVIER_RECORD_HISTORIES=1 uv run pytest -m integration \
  tests/integration/test_chat_workflow.py -k history_replays
```

## Client

### Static checks and build

```bash
cd apps/client
pnpm lint        # Biome, and Prettier for Markdown and YAML
pnpm typecheck   # pnpm gen, then tsc --noEmit
pnpm build
```

`pnpm typecheck` regenerates the API types from the server first, so it needs uv
and the server's dependencies; it uses `apps/server/settings.example.yaml` and
connects to nothing. `pnpm fmt` applies Biome and Prettier fixes.

### Unit tests

```bash
cd apps/client
pnpm test
```

Vitest runs `src/**/*.test.ts` in Node, next to the code they test. The current
tests cover pure logic: cron expressions and schedule timing in
`src/features/schedules/`. There are no component tests yet, and no DOM
environment is configured.

### End-to-end tests

Playwright tests in `apps/client/tests/` drive Chromium against the running
stack:

| File              | Covers                                                                                                                |
| ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| `auth.spec.ts`    | sign-in with a redirect back, a wrong password, sign-out                                                              |
| `app.spec.ts`     | every navigation section, the not-found page, the theme surviving a reload                                            |
| `gateway.spec.ts` | a fresh CSP nonce per document, the API and its docs behind Traefik, accepted hosts, `/api` never reaching the client |

They do not start agent turns, so no model provider is called.

1. Start the stack as described in the [README](../README.md#quick-start) and
   wait until `docker compose ps` shows it healthy.
2. Install Playwright's Chromium once:

   ```bash
   cd apps/client
   pnpm exec playwright install chromium
   ```

3. Run the tests:

   ```bash
   pnpm test:e2e
   pnpm test:e2e tests/auth.spec.ts   # one file
   ```

`XAVIER_URL` sets the address (default `http://localhost:8080`). The browser
signs in as `e2e` (`XAVIER_USERNAME`, default `e2e`; `XAVIER_PASSWORD`, default
`e2e-password`). When that sign-in answers 401, the Playwright global setup
creates the user with `xavier users create` in the server container
(`--name
E2E --password-stdin`), or runs `xavier users set-password` when the
username already exists. Traces of failed tests are kept in `test-results/`;
open one with `pnpm exec playwright show-trace <trace.zip>`.

## Before a change is done

- Python: `ruff check`, `ruff format --check`, `ty check` and the relevant
  pytest tests; the full unit suite for shared code and public contracts.
- Client: `pnpm lint`, `pnpm typecheck`, `pnpm test` and `pnpm build`.
- Integration and end-to-end tests when the change touches the stores, Temporal,
  Docker, the API contract or routing.

`AGENTS.md` holds the binding version of these rules.
