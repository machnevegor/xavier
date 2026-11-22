# Security model

## Trust

- **Signed-in users** are trusted with what they own. A user is a row in
  `users`: a username, a name, an argon2id password hash and a soft delete.
  Users are created with the CLI. A resource belongs to its creator
  (`created_by`); another user's resource answers 404.
- **The model and everything it reads are untrusted.** Web pages, API responses,
  files and command output can carry instructions (prompt injection). The
  defences below do not rely on the model behaving well: they bound what any
  tool call can do.
- **The host and Docker** are trusted. The worker holds the Docker socket, which
  is equivalent to root on the host; protect the host accordingly.

## Network exposure

- Every published port binds to 127.0.0.1: Traefik to the API and Web UI (8080),
  Temporal (7233) and its UI (8233), PostgreSQL (5432), S3 (9000, 9001). To
  reach Xavier from elsewhere, put a TLS reverse proxy or a VPN in front of
  port 8080. Traefik overwrites incoming `X-Forwarded-*` headers, and `server`
  is not published, so it trusts them.
- Traefik reads the Docker socket read-only and routes only containers of the
  `xavier` compose project that set `traefik.enable`.
- `server` has no Docker socket. Only `worker` and Traefik do.
- `server`, `worker`, `client` and `traefik` run with a read-only root
  filesystem, all capabilities dropped and `no-new-privileges`.
- The worker's internal server (screen relay, workspace files) requires
  `worker.internal_token`.

## Authorization

- Sign-in is `POST /api/v1/auth/session` with `{data: {username, password}}`. It
  returns 201 and sets the cookie `xavier_session`: `HttpOnly`,
  `SameSite=Strict`, and `Secure` when the API is served over HTTPS. The cookie
  value is stored only as a SHA-256 hash. `DELETE /api/v1/auth/session` signs
  out. `GET /api/v1/auth/me` returns the user. `PATCH /api/v1/auth/me` with
  `{data: {locale}}` saves `en` or `ru` on that user; a browser session and an
  API key can both change the caller's own profile. Sessions last
  `auth.session_ttl` (P30D) and end after `auth.session_idle_ttl` (P7D) without
  a request.
- `PATCH /api/v1/auth/me/password` verifies the current password, replaces it
  and revokes every other browser session. It requires a browser session; the
  current session remains valid.
- `GET /api/v1/sessions` and `DELETE /api/v1/sessions/{id}` list and revoke
  signed-in devices. `POST /api/v1/api-keys` creates a key; the full value
  `xv_<prefix>_<secret>` is returned once in `key`, and only its hash is stored.
  `GET /api/v1/api-keys` lists keys and `DELETE /api/v1/api-keys/{id}` revokes
  one. Both collections require a browser session. A request authenticated with
  an API key gets 403.
- API clients send `Authorization: Bearer xv_…` and act as the user who created
  the key.
- `xavier users set-password` signs out that user's sessions.
  `xavier users
  delete` soft-deletes the user and revokes their sessions and
  keys.
- Web UI documents carry a Content Security Policy that admits only the app's
  own scripts and the inline scripts stamped with a per-request nonce, `data:`
  images (noVNC draws screen updates with them) and connections to the same
  origin. API responses carry
  `Content-Security-Policy: default-src 'self'; img-src 'self' data:`. Both send
  `X-Content-Type-Options: nosniff`. Artifacts other than raster images are
  downloaded as attachments.

## Policy

Every tool call is an action evaluated by the policy before it runs
(`apps/server/src/policy/engine.py`). A tool rates each call by **risk**:

| Risk        | Examples                                                                                                                           |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `read`      | `web_fetch`, `watch_history`, `files list/read`, `screen screenshot`, waiting on a job                                             |
| `write`     | `code_run`, `terminal`, `browser`, `screen` input, `files export/import/write/edit`, `watch_draft`, cancelling a job               |
| `sensitive` | typing into a password field of the browser; revising a published Observe source to new hosts, another credential or higher limits |
| `forbidden` | always denied                                                                                                                      |

The chat **mode** gives the effect when no rule matches:

| Mode                   | `read` | `write` | `sensitive` |
| ---------------------- | ------ | ------- | ----------- |
| `autonomous`           | allow  | allow   | ask         |
| `supervised` (default) | allow  | ask     | ask         |
| `read_only`            | allow  | deny    | deny        |

**Rules** (`GET /policy/rules`; `filter[chat_id]` for a chat,
`filter[is_global]` for the global page) match a tool name pattern and
optionally a resource pattern (a URL, a host or a `/workspace` path) and set
`allow`, `ask` or `deny`. The highest priority wins, then the more specific rule
(chat scope, with a resource pattern, a longer tool pattern); on a full tie
`deny` beats `ask` beats `allow`. A denied call reaches nothing: an instruction
injected into a page cannot make a call that a rule denies.

Arguments shown in the feed and approval cards are redacted: text typed into
password fields and credential values never appear.

## Isolation of code and the Computer

Execute runs and the Computer are Docker containers started by the worker:

| Property            | Execute run                                                                               | Computer                                                                |
| ------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| User                | `1000:1000`                                                                               | `1000:1000`                                                             |
| Root filesystem     | read-only                                                                                 | read-only                                                               |
| Capabilities        | none                                                                                      | none                                                                    |
| `no-new-privileges` | yes                                                                                       | yes                                                                     |
| seccomp             | Docker default                                                                            | Docker default plus `clone`, `unshare`, `chroot` for Chromium's sandbox |
| Network             | none, or `xv-sandbox` when the run asks for it and the chat allows it (`execute_network`) | `xv-sandbox`                                                            |
| Limits              | `execute.*`: CPU, memory without swap, PIDs, `/tmp` size, duration, output size           | `computer.*`: CPU, memory without swap, PIDs                            |
| Persistent files    | the chat's `/workspace`                                                                   | the chat's `/workspace` and home                                        |

Chromium runs with its own sandbox: renderers live in separate user and PID
namespaces, which the kernel confines to the container's unprivileged user. The
extra seccomp calls are allowed only for the Computer. See
[operations](operations.md#chromium-sandbox-on-linux-hosts) for hosts that
restrict user namespaces.

Its enterprise policy (`apps/server/images/desktop/chromium-policy.json`) blocks
loopback, cloud metadata and `file://` pages, except files under `/workspace`,
so the agent can open the HTML it writes there.

## Egress

The `xv-sandbox` bridge reaches the public internet only. `firewall-init`
(`apps/server/images/firewall/apply.sh`) installs rules for traffic entering
from the bridge:

- drop destinations in private, shared, loopback, link-local (including cloud
  metadata), reserved, documentation, benchmark and multicast IPv4 ranges;
- drop all IPv6;
- drop everything addressed to the host itself (`INPUT`), so sandboxes cannot
  reach host services or published ports.

With Docker's iptables backend the rules live in the chains `XAVIER-SANDBOX`
(jumped to from `DOCKER-USER`) and `XAVIER-SANDBOX-IN`; with the nftables
backend in the table `inet xavier_sandbox`, which runs before Docker's chains.
On Docker Desktop they are installed in its Linux VM. The worker verifies the
rules at start with a probe container on the bridge and refuses to start if any
internal address, the host gateway, the worker's internal port or a configured
service is reachable.

## Fetches from the worker

`web_fetch` in `http` mode and Observe polls run in the worker process, which
can reach internal services. Every URL and every redirect is checked
(`apps/server/src/policy/urls.py`): only `http` and `https`, the host must
resolve to public addresses only, and the connection is pinned to the checked
address, so DNS rebinding cannot redirect it. Browser mode renders in a
throwaway Computer without volumes on the sandbox bridge. Response size and time
are limited by `observe.*`.

## Secrets

- `settings.yaml` holds all secrets; it is mounted read-only and never
  committed.
- After loading settings the worker removes `XAVIER_*` variables from its
  environment. The Harness runtime gets only its system prompt and the provider
  key of its model.
- Credentials are encrypted with `observe.credentials_key` (Fernet). A
  credential is a `token` or a `login`, limited to host patterns, and the secret
  is never returned. `grant_agent` lets the agent send a token in `web_fetch`
  headers or type a login into the Computer's browser, only on those hosts.
  `grant_watch` lets Watch send a token to matching hosts and refresh it only
  over HTTPS on a matching host. `grant_env` names an environment variable
  injected only into that chat's `code_run`; use is audited and the value is
  redacted. The agent refers to a credential by id and never sees the value.
  Rotation and revocation are first-class.
- Approvals may create standing allow rules with scope (`once`, `chat`,
  `always`) and resource breadth (`exact`, `host`, `prefix`). Grouped decisions
  apply several actions of one chat in order.
- Every turn gets its own MCP secret, revoked when the turn ends; a stale
  attempt cannot record actions.
- Assistant HTTPS images are served only through the media proxy for URLs
  recorded on that event; workspace `file:///workspace` embeds become chat
  artifacts, so they remain after hibernation.

## Known limits

- Every user has the same authority over their own resources. Another user's
  resource answers 404.
- `grant_env` places a credential's value in the environment of the chat's
  `code_run`. The executed code can read it and send it anywhere.
- The worker's Docker socket makes a worker compromise a host compromise.
- Sandboxes with network can reach any public address; restrict hosts with
  policy rules on the tools, not with the firewall.
- Code and the Computer share the kernel with the host; there is no VM-level
  isolation.
