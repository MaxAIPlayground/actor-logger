# Telemetry delivery: the design contract

Why `webhook.py` looks the way it does. Read this before changing any timeout, retry count, or
the `atexit` hook — every number here was paid for by an incident.

## The contract

**Telemetry must never tax the product path.** An actor's exit is billed compute. A slow or hung
telemetry endpoint must not charge the fleet to deliver an observability event that no user reads.

**Losing an event is acceptable. Not knowing you lost it is not.**

## Why terminal events cannot be pure fire-and-forget

The instinct "telemetry should be fire-and-forget" is right, and `log_start` / `log_event` already
are (`post(wait=False)`).

Terminal events (`log_error`, `log_complete`) are the exception, and not by choice: they fire at the
last instant before the process exits. There is no "later" to flush into — the container dies. For
them, fire-and-forget means forget.

So the question is not wait-vs-don't. It is **how big the exit budget is**.

## The exit budget

| knob | env | default | why |
|---|---|---|---|
| per-attempt timeout | `ACTOR_LOG_POST_TIMEOUT` | `3s` | measured round-trip (DNS+TCP+TLS+dispatch) is **0.11-0.19s**, so 3s is >10x headroom even allowing for cross-region RTT and the DB insert |
| attempts | `ACTOR_LOG_POST_TRIES` | `3` | a blip fails in milliseconds, so retrying is nearly free |
| join on waited events | `ACTOR_LOG_JOIN_TIMEOUT` | `12s` | must cover 3 x 3s + 1.5s backoff = 10.5s, and nothing more |
| exit flush | `ACTOR_LOG_FLUSH_TIMEOUT` | `5s` | bounded chance for fire-and-forget POSTs to land |

**Worst case at exit: ~10.5s. Healthy: ~0.2s.**

All are env-tunable so a single actor can be adjusted **without touching this library**, which the
whole fleet pins at `@master`. Explicit constructor args still win, so a long-lived non-actor
consumer that can afford to wait may opt out entirely:

```python
WebhookLogger(timeout=30, join_timeout=99)   # not an actor; delivery matters more than exit latency
```

`test_exit_budget_is_bounded_on_both_sides` enforces this and is **two-sided on purpose**:
- **lower** — `join_timeout` must cover the full retry budget, or a waited POST dies mid-retry.
- **upper** — and it must stay `<=15s`. **35s shipped here once.** This guard exists so it can't again.

## Why losing an event is acceptable

Loss is already detectable downstream: the telemetry DB reconciles `N start · M done` per actor and
classifies the delta as `no-terminal` (`clearpath db health`). Detecting loss after the fact is far
cheaper than every run paying to prevent it.

This is the load-bearing argument for the tight budget. If that reconciliation ever goes away, the
trade-off changes and this doc is wrong.

Corollary: the **`atexit` flush is the important half, not a long join.** A run whose `actor_start`
is lost is invisible to that reconciliation entirely — it never shows up as a `start` at all. Saving
`log_start` is what keeps loss visible.

## The three loss modes this replaced

Before v0.3.0 a failing actor run could produce **no telemetry at all** — not even `actor_start` —
on a code path that explicitly calls `log_error()`. `errors` then reads empty for a bug that is
really failing a double-digit percentage of runs, which is worse than useless: it actively points
an investigation the wrong way.

1. **No retry.** One attempt; a single blip lost the event forever.
2. **Fire-and-forget died at exit.** Daemon threads are killed at process exit, so a fast-failing
   run lost its `actor_start`. Fixed by tracking in-flight POSTs + an `atexit` join.
3. **`join_timeout` (5) < `timeout` (10).** A waited event was abandoned mid-flight even though the
   POST might still have succeeded, then killed at exit.

## Gotchas

- **`urlopen` RAISES `HTTPError` for >=400**, it does not return a response. Status classification
  must live in the `except urllib.error.HTTPError` branch. A `resp.status != 200` check after the
  `with` can never see a real 4xx.
- **4xx is not retried.** A rejected payload or a bad key fails identically every time; retrying
  only burns the budget.
- **Failures stay on `logger.debug`, deliberately.** This runs inside actors whose user-visible run
  log must never expose internals. Telemetry problems must not surface there — do not "helpfully"
  raise these to warning/stderr.
- **`master` IS the fleet's next build.** Consumers pin `actor-logger @ git+...@master`, so anything
  merged here reaches every actor on its next build, with no version gate in between. CI on master
  is that gate. Note Docker layer caching means a push may or may not land on any given build — the
  build log's `Resolved ... to commit <sha>` line is the only ground truth for what an actor runs.
