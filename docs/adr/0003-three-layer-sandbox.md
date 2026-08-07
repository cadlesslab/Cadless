# ADR-0003: Three sandbox layers for generated code, and no kernel isolation

## Status

Accepted.

## Context

The whole point of the tool is to execute code an LLM just wrote. That code
is untrusted by definition — and catalog items imported from elsewhere are
executable code too. We need isolation that is strong enough for a
local-first tool without dragging in infrastructure most contributors cannot
run.

## Decision

Defense in depth, three layers, each catching what the previous one cannot:

1. **Static AST gate, before anything runs** —
   `cadless/validation.py:validate_code` walks the AST and enforces the
   policy data in `cadless/api_subset.py`: imports limited to an allow-list
   (`build123d`, `math`, `copy`), banned names rejected (`eval`, `exec`,
   `open`, `__import__`, `getattr`, …), all dunder attribute access blocked
   (closing the `().__class__.__bases__` escape family), and a `result`
   variable required. Catalog step code passes the same gate as generated
   code.
2. **Process limits** — execution happens in a fresh subprocess
   (`cadless/worker.py`) with a POSIX CPU rlimit and a wall-clock timeout.
   `RLIMIT_AS` is deliberately **not** set: OCCT reserves a very large
   virtual address range up front, so an address-space cap produces
   thrashing, not safety.
3. **Container** — in the compose stack the worker runs with no egress (an
   internal-only network), a read-only filesystem with tmpfs `/tmp`, cgroup
   memory/CPU/pid limits, and a non-root user. The cgroup is where the real
   memory limit lives.

**There is no fourth layer.** Containers share the host kernel; we evaluated
and did not adopt gVisor/microVM isolation.

## Consequences

- The stack is safe for its intended posture — a tool you run locally, on
  your own machine, with your own prompts — and that posture is stated
  bluntly in the README rather than softened.
- It is **not** hardened for multi-tenant or internet-facing deployment.
  Anyone hosting it for others must add kernel-level isolation (and
  authentication — see [ADR-0004](./0004-local-first.md)).
- The AST gate is a real security boundary and must be treated as one:
  loosening the allow-list is a security decision, not a convenience patch.
- Keeping build123d imports confined to the execution child and the
  exporters is what makes "validate before execute" trustworthy — nothing
  upstream ever runs the untrusted code by accident.
