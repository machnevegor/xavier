# Observe

Observe tracks websites and APIs and tells the operator when something changes.
AI can help define what to watch; the operator reviews and publishes the rules.
Polling is then deterministic: no model runs during a poll, so the result does
not depend on a prompt and costs no tokens.

## Sources

A source (`apps/server/src/observe/spec.py`) is a specification of what to
fetch, what to observe in the response and when:

| Field                              | Meaning                                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------------------------------- |
| `url`, `method`, `headers`, `body` | the request; `GET` or `POST`. Secret headers are refused: they belong in a credential              |
| `credential_id`                    | a stored credential added to every request                                                         |
| `mode`                             | `http` for plain requests, `browser` to render pages that need JavaScript                          |
| `extract`                          | what to observe: `jsonpath` over JSON, or a `css` selector over HTML with an optional `attribute`  |
| `normalize`                        | noise removal before comparison: `ignore` JSONPaths such as `$..updated_at`, `sort` lists by a key |
| `children`                         | rules that discover child resources in the observed value and follow them                          |
| `limits`                           | response size, time, depth and fan-out, capped by `observe.*`                                      |
| `schedule`                         | `interval` (at least one minute) or `cron` with a `time_zone`                                      |

A source is `draft`, `published` or `paused`. Only published sources are polled.
A revision of a published source keeps the current rules running until the
revision is published.

### Child resources

A child rule selects values from its parent's observed value with JSONPath and
builds a URL for each from a template, for example
`https://api.example.com/items/{value}`. Each child has the resource key
`<rule>:<value>` and its own history; the source URL itself has the key `""`.
Rules can nest or apply recursively, for paginated or tree-shaped APIs.

- A child is fetched once per poll, so cycles terminate. A poll covers at most
  1000 resources.
- Depth and fan-out are limited; beyond the fan-out only the first children are
  followed. Hitting a limit raises a `limit` alert and makes the poll
  incomplete.
- A rule can name the JSONPath of the total the parent declares (`total`); if
  the enumeration does not match it, the poll is incomplete and a `limit` alert
  says so.
- Only after a complete poll are children that were not enumerated reported as
  `gone`. An incomplete poll never reports anything gone.

## Polling

Each published source has a Temporal Schedule with the skip overlap policy: a
poll that is still running is not started again. Each run is an
`ObservePollWorkflow`:

1. **Fetch** the source URL. `http` mode uses the worker's HTTP client with the
   URL, DNS and redirect checks described in
   [security](security.md#fetches-from-the-worker); `browser` mode renders the
   page in a throwaway Computer without volumes.
2. **Extract and normalize** the observed value, and compute its fingerprint,
   the SHA-256 of the canonical JSON.
3. **Store a version** only if the fingerprint changed. The value is stored in
   S3 by its fingerprint, so identical values are stored once. The version
   records its value changes and the inferred structure (fields, types and
   variants) with the structural changes against the previous version.
4. **Raise alerts** for what changed.
5. **Follow children** level by level, fetching up to 8 resources at once.

`POST /sources/{id}/check` runs a poll outside the timetable.

## Alerts

| Kind             | Raised when                                                                   |
| ---------------- | ----------------------------------------------------------------------------- |
| `changed`        | a new version differs from the previous one                                   |
| `schema_changed` | the structure of the value changed: fields added, removed or retyped          |
| `gone`           | a resource answers 404 or 410, or a complete poll no longer enumerates it     |
| `unavailable`    | a fetch fails, answers an unexpected status, or the extraction does not match |
| `auth_lost`      | the credential is rejected and could not be renewed                           |
| `limit`          | a size, depth, fan-out or resource limit stopped part of the poll             |
| `stale`          | two scheduled polls passed without a successful one                           |

Alerts move `open → acknowledged → resolved`. An unresolved alert of the same
kind for the same resource counts repeats instead of opening a new alert.
`unavailable` and `gone` resolve on their own once the resource answers again.

**Subscriptions** deliver new alerts: to the Web UI, or to a webhook as a JSON
POST signed with HMAC-SHA256 of the subscription's secret in
`X-Xavier-Signature: t=<unix time>,v1=<hex>`. A subscription can be limited to
one source and to some alert kinds; webhook deliveries are retried.

## Credentials

A credential is a `token` (a value sent in a header, only to matching hosts) or
a `login` (a username and password for the Computer's browser). Watch sends a
token only, and only to a host that matches the credential, and refreshes it
only over HTTPS on a matching host. Secrets are encrypted with Fernet under
`observe.credentials_key`; the API accepts them write-only and never returns
them, and the agent refers to credentials only by id. Versions are kept per
credential, so data seen with one credential is never compared with data seen
with another.

When a request answers 401 or 403, a token granted to Watch is refreshed once.
If it is still rejected, every published source that uses it is paused with an
`auth_lost` alert; other sources keep running. Replacing the secret resumes
them.

## The agent's role

In a chat with the `observe` tool group the agent can:

- fetch a page or API once with `web_fetch` to explore it and test an
  extraction;
- create or revise a draft with `watch_draft`; widening a published source to
  new hosts, another credential or higher limits is `sensitive` and needs an
  approval;
- read the history of a source with `watch_history`.

A draft is polled only after the operator publishes it, in the **Watch** section
of the Web UI or with `POST /sources/{id}/publish`. Sources can also be created
directly:

```bash
curl -sS -X POST http://localhost:8080/api/v1/sources \
  -H "Authorization: Bearer $XAVIER_API_KEY" -H 'Content-Type: application/json' -d '{
  "data": {
    "name": "Quotes",
    "spec": {
      "url": "https://quotes.toscrape.com/",
      "extract": {"css": "span.text"},
      "schedule": {"interval": "PT1H"}
    }
  }
}'
curl -sS -X POST http://localhost:8080/api/v1/sources/<source_id>/publish \
  -H "Authorization: Bearer $XAVIER_API_KEY"
```

Versions, diffs and alerts are available under `/api/v1/sources/{id}` and
`/api/v1/alerts`, and in the **Watch** section.
