# Development

This guide covers the repository layout, the development environments and the
everyday commands. Checks and tests are described in [testing](testing.md);
contribution rules for code changes are in [`AGENTS.md`](../AGENTS.md).

## Repository layout

| Path                          | Contents                                                                                                    |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `apps/server/`                | the Python backend: REST API, Temporal worker, CLI; Python 3.14, managed with uv                            |
| `apps/server/src/`            | the code, by subsystem; see [architecture](architecture.md#code-layout)                                     |
| `apps/server/images/`         | the sandbox, desktop and firewall images                                                                    |
| `apps/server/tests/`          | `unit/`, `integration/` and `fixtures/`                                                                     |
| `apps/server/settings.*.yaml` | `settings.example.yaml` to copy, `settings.schema.yaml` for editors; `settings.yaml` is local               |
| `apps/client/`                | the Web UI: TypeScript, TanStack Start, shadcn/ui; Node.js 22 and pnpm; see [web-ui](web-ui.md#code-layout) |
| `docker-compose.yaml`         | the apps behind Traefik                                                                                     |
| `infra/docker-compose.yml`    | PostgreSQL, Temporal with its UI, RustFS                                                                    |
| `docker-compose.test.yaml`    | the infrastructure of the integration tests                                                                 |
| `scripts/`                    | init scripts and configuration of PostgreSQL and Temporal                                                   |
| `.devcontainer/`              | the Dev Container                                                                                           |
| `docs/`                       | this documentation                                                                                          |

Each app has its own `Dockerfile` and toolchain; they share only the API
contract, which the client generates from the server.

## Environments

### Everything in Docker

The [quick start](../README.md#quick-start) runs the infrastructure and both
apps in Docker Compose. It is the only setup in which the whole platform works:
the worker needs the Docker socket to start sandboxes and Computers. Rebuild
after a change:

```bash
docker compose up -d --build server worker   # the server image is shared
docker compose up -d --build client
```

### Dev Container

`.devcontainer/` describes a Debian container with Node.js 22, pnpm and uv.
Before it starts, `.devcontainer/infra.sh` brings up `infra/docker-compose.yml`
on the host, asking first; `INFRA_AUTOSTART=1` or `INFRA_AUTOSTART=0` answers
without the prompt. The container joins the `xavier` network and reaches the
infrastructure by service name, through `XAVIER_DATABASE__HOST`,
`XAVIER_S3__ENDPOINT_URL` and `XAVIER_TEMPORAL__ADDRESS`. Dependencies are
installed when the container is created.

Inside the container, with `apps/server/settings.yaml` in place:

```bash
cd apps/server
uv run xavier migrate
uv run xavier init-storage
uv run xavier api               # http://localhost:8000

cd apps/client
pnpm dev                        # http://localhost:3000
```

The container has no Docker socket, so it cannot run the worker or the
integration tests. Run the worker in Compose against the same infrastructure
(`docker compose up -d worker`), and the integration tests on the host.

### On the host

Install [uv](https://docs.astral.sh/uv/), Node.js 22 and pnpm, start
`infra/docker-compose.yml`, and use the same commands as in the Dev Container.
The defaults in `settings.example.yaml` point at the infrastructure's ports on
`127.0.0.1`.

## Server

The `xavier` command (`uv run xavier` in `apps/server`) reads `settings.yaml`
from the current directory, or the file in `--settings` or
`XAVIER_SETTINGS_FILE`:

| Command         | Purpose                                                                         |
| --------------- | ------------------------------------------------------------------------------- |
| `api`           | serve the REST API                                                              |
| `worker`        | run the Temporal worker and the agent gateway                                   |
| `migrate`       | upgrade the database schema                                                     |
| `init-storage`  | create the buckets and their lifecycle rules                                    |
| `openapi`       | print the OpenAPI document                                                      |
| `config-schema` | print the JSON Schema of the settings                                           |
| `bench`         | measure Compute, Execute and reference chats; see [performance](performance.md) |

After changing the settings models in `src/config.py`, regenerate the schema; a
unit test fails while it is stale:

```bash
uv run xavier config-schema > settings.schema.yaml
```

Database changes are Alembic migrations in `src/db/migrations/`;
`xavier migrate` applies them, and an integration test checks that they match
the models.

Python dependencies change only through uv (`uv add`, `uv add --group dev`,
`uv remove`, `uv lock --upgrade-package`).

## Client

```bash
cd apps/client
pnpm dev     # http://localhost:3000, proxies /api to XAVIER_API
pnpm gen     # regenerate the API types after a server contract change
```

`XAVIER_API` defaults to `http://127.0.0.1:8000`, the server of the Dev
Container or the host. To develop the Web UI against the Compose stack instead,
point it at Traefik:

```bash
XAVIER_API=http://127.0.0.1:8080 pnpm dev
```

New shadcn/ui components are added with `pnpm exec shadcn add <component>`.
Dependencies change only through `pnpm add` and `pnpm remove`.
