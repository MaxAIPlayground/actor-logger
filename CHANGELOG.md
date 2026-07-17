# Changelog

Consumers pin this library as `actor-logger @ git+...@master`, so every entry here reaches the
whole actor fleet on its next build. There is no version gate in between.

## 0.3.1 — 2026-07-17

- **Cap the telemetry exit budget.** `timeout` 10s→3s, `join_timeout` 35s→12s. Worst case at exit
  ~31s→**~10.5s**; healthy ~0.2s. 0.3.0 overcorrected: an actor's exit is billed compute, so a hung
  endpoint charged the fleet to deliver an observability event. 3s is measured, not guessed — a real
  round-trip is 0.11-0.19s.
- Both are env-tunable (`ACTOR_LOG_POST_TIMEOUT`, `ACTOR_LOG_JOIN_TIMEOUT`) so one actor can be
  adjusted without touching this library; explicit constructor args still win.
- `test_exit_budget_is_bounded_on_both_sides`: `join_timeout` must cover the retry budget AND stay
  ≤15s.
- Rationale + the numbers: [docs/2607-telemetry-delivery.md](docs/2607-telemetry-delivery.md).

## 0.3.0 — 2026-07-16

- **Stop losing telemetry silently.** A failing actor run could produce no telemetry at all — not
  even `actor_start` — on a path that explicitly calls `log_error()`, making error counts a lower
  bound rather than a measurement.
  - `post_sync` retries transient failures and 5xx (`ACTOR_LOG_POST_TRIES`, default 3, 0.5s/1s
    backoff). 4xx is not retried.
  - In-flight POSTs are tracked and joined by an `atexit` hook (`ACTOR_LOG_FLUSH_TIMEOUT`, default
    5s), so `log_start`'s daemon thread is no longer killed mid-send.
  - Fixed status classification: `urlopen` raises `HTTPError` for ≥400 rather than returning a
    response, so the old post-`with` status check could never see a real 4xx.
- CI: tests now run on every push/PR to master (3.10 + 3.12).
