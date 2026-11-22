# Performance

Not measured yet: this file is replaced by the first run of `xavier bench`.

## Method

`uv run xavier bench --output ../../docs/performance.md` in `apps/server` on a
host of the running stack, with its `settings.yaml`, rewrites this file. It
starts Computers and sandbox containers through the services the worker uses,
calls the tools directly without a model, and removes everything it created.
Times are wall-clock; percentiles are nearest-rank.

The report has these sections:

- **Computer start** — pool refill, activation with and without a pool Computer,
  the first action after activation, hibernation and wake from a checkpoint.
- **Actions** — p50 and p95 of `browser`, `screen` and `terminal` calls through
  the tools, with the GUI lease and the channel to `computerd`.
- **Scenario** — a fixed multi-step browsing scenario, step by step.
- **Execute** — `code_run` of `print(1)` in a new sandbox container, with and
  without the workspace, and workspace wake-ups from a checkpoint.
- **Agent** — turns, LLM calls, tool calls and cost of reference chats. Before
  running the bench, create chats through the API or the Web UI with these first
  messages (the title is taken from the first message), with the matching tool
  group enabled:
  - Reference: Observe — watch
    https://api.github.com/repos/python/cpython/releases and alert on a new
    release.
  - Reference: Execute — in Python, compute the mean and median of the integers
    1 to 1000 and save them to /workspace/stats.json.
  - Reference: Compute — open https://quotes.toscrape.com/js/ in the browser and
    list the authors of the first five quotes.
