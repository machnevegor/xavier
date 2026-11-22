# Operations

## Supported hosts

- macOS with Docker Desktop, including Apple Silicon. The firewall rules are
  installed in the Docker Desktop VM, where the sandbox bridge lives.
- Linux with Docker Engine and either the iptables or the nftables backend.

## Configuration

All settings live in `apps/server/settings.yaml`, copied from
`settings.example.yaml`. `settings.schema.yaml` next to it describes every key
and gives editors completion. Any key can be overridden by an environment
variable `XAVIER_<SECTION>__<KEY>`, for example `XAVIER_LOGGING__LEVEL=DEBUG`;
`docker-compose.yaml` sets the container addresses this way.

| Section                            | What it configures                                                                       |
| ---------------------------------- | ---------------------------------------------------------------------------------------- |
| `api`, `worker`                    | listen addresses, worker capacity and internal token                                     |
| `database`, `s3`, `temporal`       | infrastructure; must match `infra/docker-compose.yml`                                    |
| `logging`                          | level and format                                                                         |
| `agent`                            | model providers, models with pricing and image input, the default model                  |
| `turn`, `approval`, `chat`         | default step limit of new chats, turn timeout, approval waiting, default mode and budget |
| `workspace`, `execute`, `computer` | volumes, sandbox and desktop images and their limits                                     |
| `observe`                          | credential encryption key, fetch limits                                                  |
| `retention`                        | how long checkpoints, deleted chats and executions are kept                              |
| `telemetry`                        | OTLP endpoint for traces and metrics                                                     |

Required secrets are `worker.internal_token`, the providers' `api_key` and
`observe.credentials_key`, a Fernet key:

```bash
cd apps/server
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Infrastructure passwords default to development values. Override them with the
variables of `infra/docker-compose.yml` (`POSTGRES_PASSWORD`,
`XAVIER_DB_PASSWORD`, `TEMPORAL_DB_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`)
and put the same values into `settings.yaml`.

Traefik serves `localhost` and `127.0.0.1` on port 8080. To serve another host
name, for example behind a reverse proxy, set `XAVIER_DOMAIN` for
`docker compose up`.

## Start, stop, upgrade

```bash
docker build -t xavier-sandbox:local apps/server/images/sandbox
docker build -t xavier-desktop:local apps/server/images/desktop
docker compose -f infra/docker-compose.yml up -d
DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose up -d --build   # Linux
docker compose up -d --build                                                # Docker Desktop
docker compose ps
docker compose logs --follow server worker client
docker compose down
docker compose -f infra/docker-compose.yml down   # keeps the volumes postgres-data and rustfs-data
```

The apps join the `xavier` network that the infrastructure creates. To upgrade,
pull the new code, rebuild the two images above and run
`docker compose up -d --build` again. The one-shot `server-migrate` and
`server-init-storage` run before `server` starts. Running chats continue after
the restart: their workflows live in Temporal, and an interrupted turn is
retried on the next worker attempt (see [recovery](agent.md#recovery)).

Firewall rules do not survive a host or Docker Desktop restart. `firewall-init`
runs on every `docker compose up`, and the worker checks the rules at start: it
runs a probe container on the sandbox bridge and refuses to start if the probe
reaches any internal address. To reinstall the rules by hand:

```bash
docker compose run --rm firewall-init
```

## Health

| Check                                | Meaning                                         |
| ------------------------------------ | ----------------------------------------------- |
| `GET /api/v1/healthz`                | `server` serves requests                        |
| `GET /api/v1/readyz`                 | PostgreSQL and Temporal answer; `503` otherwise |
| Temporal UI, <http://127.0.0.1:8233> | workflows, schedules, activity retries          |
| Web UI **Usage**                     | cost, turns, tokens and agent time by period    |

When PostgreSQL, S3 or Temporal is down, API requests answer `503`. A message
sent while Temporal is down is stored as `queued` and delivered with the next
message or command once Temporal is back.

## Several workers

Workers can run on several hosts against the same PostgreSQL, S3 and Temporal.
Requirements:

- every worker has a unique, stable `worker.id` (the hostname by default);
- `agent.cwd` is the same absolute path on every worker (the compose worker uses
  its default);
- the images `xavier-sandbox:local` and `xavier-desktop:local` exist on every
  host;
- `firewall-init` runs on every host;
- `server` can reach every worker's internal server (`worker.internal_host`,
  `worker.internal_port`).

A turn runs on the host where the chat's workspace is active; a hibernated
workspace wakes on whichever worker takes the turn. A host runs at most
`worker.max_workspaces` active workspaces and hibernates the least recently used
idle one to make room. If a worker dies, its turns are retried elsewhere after
the activity heartbeat times out; its workspaces fall back to their last
checkpoint.

## Data and backups

| Volume or bucket                                              | Back up with                                          |
| ------------------------------------------------------------- | ----------------------------------------------------- |
| `postgres-data` (Xavier and Temporal databases)               | `docker compose exec postgres pg_dumpall -U postgres` |
| `rustfs-data` (buckets `xavier-artifacts`, `xavier-temporal`) | any S3 client against `http://127.0.0.1:9000`         |

Restore both from the same point in time: PostgreSQL holds the references to S3
objects.

Retention (`retention.*`) is enforced by `MaintenanceWorkflow`, which runs on a
Temporal Schedule the worker creates at start. Temporal keeps workflow histories
for 7 days after completion; large Temporal payloads in `xavier-temporal` expire
after `s3.temporal_payload_ttl_days`.

## Secrets

`apps/server/settings.yaml` holds the internal token, provider keys and the
credentials key. Users are created with the CLI:

```bash
xavier users create <username> [--name NAME] [--password-stdin]
xavier users list
xavier users set-password <username> [--password-stdin]
xavier users delete <username>
```

`users create` and `users set-password` prompt for the password unless
`--password-stdin` is set. `users set-password` signs out every session of that
user. `users delete` soft-deletes the user and revokes their sessions and API
keys. The file is mounted read-only into the containers and must never be
committed. Changing `observe.credentials_key` makes stored credentials
unreadable; enter them again afterwards.

The Harness runtime inherits the worker environment, so the worker environment
holds no secrets: provider keys are passed to the runtime per turn.

## Chromium sandbox on Linux hosts

Chromium in the Computer keeps its own sandbox enabled. It needs unprivileged
user namespaces, which the Computer's seccomp profile allows (Docker's default
profile plus `clone`, `unshare` and `chroot`). Ubuntu 23.10 and later restrict
unprivileged user namespaces through AppArmor
(`kernel.apparmor_restrict_unprivileged_userns=1`); on such hosts Chromium
cannot start its sandbox and the Computer fails. Docker Desktop and Debian are
not affected. On such a host, either allow user namespaces for the container
runtime with an AppArmor profile, or set the sysctl to `0`, after weighing the
kernel attack surface it opens.

## Performance measurements

```bash
cd apps/server && uv run xavier bench --output ../../docs/performance.md
```

The command runs against the local stack with `apps/server/settings.yaml` and
rewrites [performance.md](performance.md). See that file for the method.

## Troubleshooting

| Symptom                                       | Cause and fix                                                                    |
| --------------------------------------------- | -------------------------------------------------------------------------------- |
| The worker exits with `FirewallOpenError`     | The sandbox bridge reaches internal addresses: run `firewall-init`               |
| The worker cannot reach Docker on Linux       | `DOCKER_GID` is not the group of `/var/run/docker.sock`                          |
| Tool calls return `host_full`                 | All `worker.max_workspaces` workspaces are busy; raise the limit or add a worker |
| Computer calls return `env_busy`              | The operator holds control of the screen; release it                             |
| `503 unavailable`                             | PostgreSQL, S3 or Temporal is unreachable; see `docker compose ps`               |
| A chat is `paused` and `resume` returns `409` | The chat budget is spent; raise `budget_usd` with `PATCH /chats/{id}`            |
