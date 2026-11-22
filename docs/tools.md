# Tools

The agent calls tools through the MCP gateway of its turn. A chat enables tool
groups (`tool_groups`): `observe`, `execute`, `compute`. The `files` group comes
with `execute` or `compute`. `execute_network` and `proactive` are separate
capabilities. `GET /api/v1/tools` returns the catalog with argument schemas; the
Web UI shows it under **Guardrails**.

Every call is recorded as an action and evaluated by the
[policy](security.md#policy) with the call's risk and resources (URLs, hosts,
`/workspace` paths, and compound command resources for `terminal`). Every result
is JSON with the `action_id`. Results over 32 KiB are stored as artifacts and
returned as a preview with the `artifact_id`.

| Tool                 | Groups           | Risk                                                                   | Resources                                          |
| -------------------- | ---------------- | ---------------------------------------------------------------------- | -------------------------------------------------- |
| `web_fetch`          | observe          | read                                                                   | URL, host                                          |
| `watch_draft`        | observe          | write; sensitive when it widens a published source                     | URL and hosts of the spec                          |
| `watch_history`      | observe          | read                                                                   | —                                                  |
| `code_run`           | execute          | write                                                                  | `/workspace` paths of `export`                     |
| `files`              | files            | read for `list`, `read`; write for `export`, `import`, `write`, `edit` | path                                               |
| `job`                | execute, compute | read for `wait`, `status`, `list`; write for `cancel`                  | —                                                  |
| `browser`            | compute          | write; sensitive when typing into a password field                     | URLs and hosts; `/workspace` paths of `file:` URLs |
| `screen`             | compute          | read for `screenshot`, `windows`; write otherwise                      | —                                                  |
| `terminal`           | compute          | write                                                                  | hosts and paths named by the command               |
| `computer`           | compute          | write                                                                  | —                                                  |
| `request_capability` | always           | write; always asks                                                     | the capability                                     |
| `request_credential` | always           | sensitive; always asks                                                 | hosts the secret may be sent to                    |
| `wakeup`             | proactive        | write                                                                  | —                                                  |

Long-running work (`code_run`, `terminal`) returns a `job_id` when it outlasts
`wait_s`; `job` waits for one or many jobs, lists them or cancels. A job keeps
running between turns; completion can wake a Proactive chat.

When the operator stops or pauses a turn, a running call of `web_fetch`,
`watch_history`, `files` or `job` ends as `cancelled`. A `files` `write`, `edit`
or `import` that already reached the workspace stays written; `list`, `read` and
`export` change nothing. Other running calls end as `uncertain`. When a worker
is lost mid-turn, the retried turn settles the calls of the lost attempt (a read
becomes `failed`, a write `uncertain`) and gets a `recovery` notice listing
them.

## Observe

### `web_fetch`

Fetch a public page or API response once and return readable text: HTML becomes
Markdown-like text with links, JSON is indented. Internal and private addresses
are refused.

| Argument  | Meaning                                                                                                |
| --------- | ------------------------------------------------------------------------------------------------------ |
| `url`     | `http` or `https` URL                                                                                  |
| `mode`    | `http` (default): a plain GET. `browser`: render in a throwaway browser, for pages built by JavaScript |
| `extract` | return only the values matched by `{"jsonpath": ...}` or `{"css": ..., "attribute": ...}`              |

### `watch_draft`

Draft deterministic monitoring: the URL, what to extract, what to ignore when
comparing, child resources to follow and the schedule. Nothing is polled until
the operator publishes the draft.

| Argument    | Meaning                                                                                                                                                                        |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`      | short name                                                                                                                                                                     |
| `spec`      | the source spec: `url`, `method`, `headers`, `body`, `mode`, `extract`, `normalize`, `children`, `credential_id`, `limits`, `schedule` (`interval` or `cron` with `time_zone`) |
| `source_id` | revise this source; a published source keeps its current rules until the revision is published                                                                                 |

### `watch_history`

Read what a source recorded.

| Argument                              | Meaning                                                                                           |
| ------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `source_id`                           | the source                                                                                        |
| `op`                                  | `latest` value, `versions` newest first, or `diff` between two versions (by default the last two) |
| `resource_key`                        | `""` for the source URL, `<rule>:<value>` for a child resource                                    |
| `from_version`, `to_version`, `limit` | range and page size                                                                               |

## Execute

### `code_run`

Run code in a fresh container with the chat's `/workspace` mounted. Everything
outside `/workspace` is discarded after the run.

| Argument   | Meaning                                                         |
| ---------- | --------------------------------------------------------------- |
| `language` | `python` (default), `node` or `bash`                            |
| `code`     | program text, or                                                |
| `command`  | a bash command line                                             |
| `network`  | internet access; refused unless the chat sets `execute_network` |
| `export`   | `/workspace` paths that become artifacts after the run          |
| `wait_s`   | seconds to wait before returning a `job_id` (default 60)        |

The same runs are available without a chat through `POST /api/v1/executions`.

### `files`

Work with files in `/workspace`, shared by code runs and the Computer.

| Argument      | Meaning                                                                                                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `op`          | `list` a directory, `read` a text file, `write` or `edit` a text file, `export` a file as an artifact for the user, `import` an artifact (such as an upload) into a file |
| `path`        | path under `/workspace`; relative paths start there. `write` and `edit` need a file, not `/workspace` itself                                                             |
| `text`        | full text `write` stores, at most 100000 characters                                                                                                                      |
| `old`, `new`  | literal fragment `edit` replaces. `new` may be empty, which deletes the match. `old` must occur once unless `replace_all`                                                |
| `replace_all` | replace every `old` match (default false)                                                                                                                                |
| `artifact_id` | the artifact to `import`                                                                                                                                                 |
| `max_chars`   | characters `read` returns (default 20000)                                                                                                                                |

### `job`

| Argument | Meaning                              |
| -------- | ------------------------------------ |
| `job_id` | returned by `code_run` or `terminal` |
| `action` | `wait` (default) or `cancel`         |
| `wait_s` | seconds to wait                      |

## Compute

The Computer starts with the first Compute call and hibernates with the
workspace. While the operator holds control of the screen, Compute calls fail
with `env_busy`.

### `browser`

Run a batch of steps in Chromium and return each step's result plus a snapshot
of the page: visible interactive elements with ids such as `e12`, the visible
text and the tabs. Steps refer to elements by the ids of the latest snapshot.
Downloads go to `/workspace/downloads`. URLs are `http` or `https`; `navigate`
and `tab_new` also open files under `/workspace` as `file:///workspace/...`.

| Argument   | Meaning                                                                                                                                                                                                                 |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `actions`  | steps in order; the batch stops at the first failed step. Each step has `action`: `navigate`, `click`, `type`, `select`, `press`, `scroll`, `wait`, `back`, `tab_new`, `tab_switch`, `tab_close`, `extract`, `download` |
| `snapshot` | return the snapshot after the steps (default true)                                                                                                                                                                      |

Text typed into password fields is redacted in the feed and makes the call
`sensitive`.

### `screen`

See and control the 1280×800 desktop.

| Argument                 | Meaning                                                                                                   |
| ------------------------ | --------------------------------------------------------------------------------------------------------- |
| `action`                 | `screenshot`, `click`, `double_click`, `move`, `drag`, `type`, `key`, `scroll`, `windows`, `focus_window` |
| `marks`                  | `screenshot`: outline the browser's interactive elements with their ids                                   |
| `x`, `y`, `to_x`, `to_y` | screen coordinates; `to_*` for `drag`                                                                     |
| `button`                 | `click` button                                                                                            |
| `text`, `keys`           | `type` text; `key` sequence such as `ctrl+l`                                                              |
| `direction`, `amount`    | `scroll`                                                                                                  |
| `window`                 | `focus_window`: id from `windows`                                                                         |

Screenshots need a model with image input.

### `terminal`

Run a bash command in the Computer as the user that owns `/workspace` and home.
Programs with windows appear on the screen. Processes stop when the workspace
hibernates; files stay.

| Argument  | Meaning                                                  |
| --------- | -------------------------------------------------------- |
| `command` | a bash command line                                      |
| `cwd`     | absolute working directory (default `/workspace`)        |
| `wait_s`  | seconds to wait before returning a `job_id` (default 60) |
