# Web UI

The Web UI (`apps/client`) is where the operator hands work to an AI model,
follows its progress, takes control of the Computer and approves consequential
actions. It also covers monitoring, schedules, code runs, guardrails and usage.
It uses only the public REST API, so everything it does can also be done through
`/api/v1`.

## Stack

| Concern        | Choice                                                                             |
| -------------- | ---------------------------------------------------------------------------------- |
| Framework      | React 19, TanStack Start on Nitro, TanStack Router with file-based routes          |
| Data           | TanStack Query; `openapi-fetch` typed from the server's OpenAPI document           |
| Styling        | Tailwind CSS v4 with CSS variables; shadcn/ui components on Base UI (`base-vega`)  |
| Icons and type | Tabler icons; IBM Plex Sans and IBM Plex Mono                                      |
| Specialized    | noVNC for the Computer screen, Recharts for usage, react-markdown and highlight.js |
| Checks         | Biome and Prettier, TypeScript, Vitest, Playwright (see [testing](testing.md))     |

API types are generated, not written: `pnpm gen` runs `xavier openapi` from
`apps/server` and writes `__generated__/openapi.json` and
`__generated__/schema.ts`, which are not committed. `pnpm typecheck` runs it
first, so a server contract change that breaks the client fails the type check.

## How it runs

- **One origin.** In Docker Compose, Traefik serves the client and routes `/api`
  to the server on the same origin. The development server proxies `/api` to
  `XAVIER_API` (default `http://127.0.0.1:8000`) the same way, including
  WebSockets.
- **Client-side data.** The server renders the document with its stylesheet and
  theme, but not the signed-in pages (`ssr: false` on the `_auth` layout route):
  the operator's data can be fetched only with the browser's session cookie, so
  the first frame shows the loading mark until the client takes over.
- **Authentication.** **Sign in** is a username and password form. It posts
  `{data: {username, password}}` to `POST /api/v1/auth/session` and receives an
  HttpOnly session cookie; the page holds neither the password nor an API key. A
  `401` sends the user back to sign-in. The interface language is English or
  Russian. Before sign-in it follows the browser and can be changed on the
  sign-in page; after sign-in the language saved on the account wins, and a
  first sign-in saves the language already on screen. Changing it never changes
  the URL. **Account** (`/account`) sets that language and lists signed-in
  devices (device, IP, last seen, revoke) and API keys (create, shown once with
  a copy button; list; revoke). The operator can also change their password; the
  current browser stays signed in and other browser sessions are revoked.
- **Content Security Policy.** In production every document gets a fresh nonce:
  only the app's own scripts and the nonce-stamped inline scripts run, images
  are limited to the origin and `data:` (noVNC draws with them), and connections
  go only to the origin and, when browser telemetry is configured, the HyperDX
  origin.
- **Live updates.** A chat's feed follows the SSE stream
  `/api/v1/chats/{id}/events/stream`. After a dropped connection it resumes from
  the last delivered event, backing off up to 15 s; `401` and `404` end the
  stream. The chat header shows the connection state.
- **Computer screen.** noVNC connects to the WebSocket
  `/api/v1/chats/{id}/computer/vnc`, view-only by default. **Take control**
  takes the GUI lease and reconnects with control; leaving the **Computer** tab
  gives control back. The screen can go fullscreen (or cover the page where the
  browser has no element fullscreen, as on iPhone), and while control is held a
  phone can raise its keyboard and type into the Computer.
- **Browser telemetry** is optional and built in only when the image is built
  with `VITE_SERVICE_NAME`, `VITE_HYPERDX_URL` and `VITE_HYPERDX_API_KEY`. It
  records traces and errors only: no session replay, no console capture, no
  request or response bodies, and text and inputs are masked.

## Information architecture

Navigation is organized by the operator's intent rather than by subsystem:

| Section    | Path          | Purpose                                                           | API                      |
| ---------- | ------------- | ----------------------------------------------------------------- | ------------------------ |
| Work       | `/`           | chats: start a task, follow and steer the agent                   | `chats`                  |
| Needs you  | `/approvals`  | actions waiting for a decision, with a count in the navigation    | `approvals`              |
| Watch      | `/watch`      | sources, versions, diffs and alerts; a new source is `/watch/new` | `observe`, `credentials` |
| Schedules  | `/schedules`  | delayed and recurring tasks; a new schedule is `/schedules/new`   | `schedules`              |
| Code runs  | `/runs`       | code runs outside chats                                           | `executions`             |
| Guardrails | `/guardrails` | policy rules, moderation modes and the tool catalog               | `policy/rules`, `tools`  |
| Usage      | `/usage`      | tokens, cost and time                                             | `metrics/summary`        |

A chat keeps a header pinned above the conversation, like a messenger: the
agent's face, the chat title and its status, which names the current tool work
while a turn runs ("Using the browser…"). The face follows the chat's real
status (working, using the Computer, waiting on a job, waiting for a decision or
an answer, paused, idle, offline) and glances down while the operator writes.
The conversation is the feed (question, credential, capability and approval
cards, copy, share, edit and regenerate on messages, embeds and media) and the
message composer. A reply shows the text of the turn's latest model step while
it runs, and keeps a partial reply when a turn ends early.

The conversation opens at its latest message and follows new entries, streamed
text and loading images while the reader is there; once they scroll up it stays
put, and a button above the composer returns to the end. That button gives a
small hop when the agent asks a question, waits for a decision or finishes a
turn (a fading ring with reduced motion).

Tapping the face or title opens the chat's profile in the same column
(`?view=profile&tab=`). The conversation stays mounted and hidden, and its
scroll position is restored on the way back. The tabs, in order, are **Media**
(the image gallery, `GET /api/v1/chats/{id}/media`), **Files** (Workspace and
Delivered), **Computer** (the screen first, diagnostics after), **Credentials**,
**Guardrails** and **Usage**. The open tab is in the URL, so the browser's back
button closes the profile; the Computer button in the header opens its tab
directly. Adding or changing a rule for this chat replaces the Guardrails tab's
list with the rule form; saving or cancelling returns to that list. **Settings**
(`?view=settings`) replace the conversation the same way. The sections are
**Model**, **Permissions**, **Limits** and a collapsible **Advanced**, with a
sticky **Save** bar once something has changed. The title stays under
**Rename**. The chat menu holds **Stop**, **Pause** or **Resume**, **Settings**,
**Rename** and **Delete**, and delete asks for confirmation. A message offers
**Copy** and **Share**; a sent message also offers **Edit** and **Regenerate**.
An answer offers **Copy** and **Share**. A deep link (`?message=<id>` or
`?event=<id>`) sets `data-highlighted` on that entry, which tints it.

On tablets and desktops the navigation is a sidebar that collapses to an icon
rail; on phones it is a bar along the bottom edge that opens a sheet with every
section.

## Code layout

| Path                     | Contents                                                                                |
| ------------------------ | --------------------------------------------------------------------------------------- |
| `src/routes/`            | file-based routes; `routeTree.gen.ts` is generated by the router plugin                 |
| `src/features/<domain>/` | screens of one domain with their queries (`api.ts`) and pure logic (`model.ts`)         |
| `src/components/`        | shared components: app shell, page layout, states, status, code and data views          |
| `src/components/ui/`     | shadcn/ui primitives, added with `pnpm exec shadcn add <component>` (`components.json`) |
| `src/api/`               | the typed REST client, formatted errors, the event stream, query defaults               |
| `src/lib/`               | formatting, theme, JSON helpers, telemetry                                              |
| `src/styles/globals.css` | design tokens, type roles and the chat prose styles                                     |
| `tests/`                 | Playwright end-to-end tests                                                             |

## Design conventions

The interface is quiet and predictable in structure, and puts the agent's work
and the operator's decisions first. These conventions are encoded in the tokens
and shared components; new screens follow them.

### Color and surfaces

Surfaces and colors carry meaning, defined once in `globals.css` for the light
and dark themes:

| Token         | Use                                                                                |
| ------------- | ---------------------------------------------------------------------------------- |
| `background`  | the page; app content and the agent's prose sit directly on it                     |
| `card`        | something of the operator's (fields, own messages, a decision) or a floating layer |
| `muted`       | verbatim machine text: code, arguments, output                                     |
| `primary`     | interaction only: the main action, focus, selection                                |
| `attention`   | a person is needed, or an outcome is unknown                                       |
| `destructive` | something failed or was refused                                                    |
| `ok`          | something is running or finished well                                              |

Everything else is neutral. Controls are flat; shadows are reserved for floating
layers. Containers are used only when grouping means something, not as
decoration.

### Status

Status is words first, never color alone. Each meaning also has its own mark
(`src/components/status-text.tsx`): a ring for idle, a dot for in progress, a
check for done, a diamond for "needs a person", a cross for failed.

### Typography

One family, IBM Plex Sans, with weight and position making the hierarchy; IBM
Plex Mono for machine text. The scale is 12 px (meta), 14 px (interface), 16 px
(reading), 20 px (page title) and 24 px (key figures). Tables and times use
tabular figures. The agent's answers are set at reading size with a 68-character
measure.

### States and feedback

- Every query renders one of three states (`src/components/states.tsx`): loading
  rows that keep the content's shape, an in-place error that says what failed
  and offers **Try again** or **Sign in**, and an empty state that says what to
  do next.
- Errors stay on screen; toasts are only for transient confirmations.
- Long-running work is labelled with what is happening.
- Destructive actions with lasting consequences are confirmed in a dialog that
  states them, as **Archive** does.

### Chat feed layers

The conversation stacks four roles on `background`, without nested decorative
cards:

| Layer        | What it is                        | Surface                                                                           |
| ------------ | --------------------------------- | --------------------------------------------------------------------------------- |
| Prose        | Agent answers and user messages   | Plain text on `background`; user text on `card`                                   |
| Run          | One turn's tool timeline          | Quiet summary line; steps on a left rule with dots                                |
| Intervention | Approvals, questions, credentials | `DecisionCard` on `card`; inputs like the question composer (`FeedComposerField`) |
| Record       | Arguments, stdout, JSON           | `FeedRecord` on muted fill; full raw JSON only under **More details**             |

Tool results use `Action` from the API (`tool`, `arguments`, `status`, `error`,
timestamps). Readable fields come from the recorded event payload; anything else
stays in **More details**. On credential requests the agent's `name`, `type`,
`hosts`, `reason` (and optional `header`, `prefix`, `username`) stay visible on
the card; empty **Advanced** fields use those arguments as placeholders and fall
back to them on save.

Shared primitives live in `src/components/feed-surface.tsx`.

### Agent control

The operator should always see what the agent is doing and be able to stop it:

- the chat header shows the status, the budget spent, the moderation mode and
  whether the Computer is on;
- **Stop**, **Pause** and **Archive** are always in the same place;
- tool calls appear as one quiet turn entry; useful output and errors stay
  visible, while arguments, policy metadata and raw records are disclosed on
  demand;
- an approval card shows the requested action, resources, risk and consequence
  (code as code), keeps exact arguments under details, and can also create an
  "always allow" rule for the same kind of call.

### Accessibility and responsiveness

- Every control is reachable by keyboard with a visible focus ring; icon-only
  buttons have accessible names.
- Live regions announce status changes such as the stream state.
- `prefers-reduced-motion` turns off transitions and animations.
- Layout adapts rather than shrinks: sidebar, rail and bottom sheet; touch
  targets are at least 48 px in the phone navigation.
- Interface text is available in English and Russian and stays literal where an
  action has consequences.
