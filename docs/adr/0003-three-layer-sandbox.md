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

1. **Static AST gate, before anything runs, and the namespace scripts run
   in** — `cadless/validation.py:validate_code` walks the AST and enforces the
   policy data in `cadless/api_subset.py`: imports limited to an allow-list
   (`build123d`, `math`, `copy`) by exact name, and names taken from build123d
   limited to the ones reviewed for scripts; banned and dunder names rejected
   wherever a name is written (`eval`, `exec`, `open`, `__import__`,
   `getattr`, …), and a builtin outside the scripts' allow-list wherever it is
   read; dunder and private attribute access blocked (closing the
   `().__class__.__bases__` escape family) along with the attributes of
   frames, generators, coroutines, tracebacks and code objects and the
   methods that look attributes up from text; no attribute set but the few
   that name, colour and place a part; build123d's file, network and font
   functions withheld; class definitions and `match` refused; and a `result`
   variable required. `cadless/script_runtime.py:run_script` runs that gate
   in the execution child before every script, then runs the script with
   allow-listed builtins and an `__import__` that hands out views of the three
   modules — the names reviewed for scripts, never a module — instead of the
   modules, refuses a script that changed one of build123d's classes, and the
   child accepts only a build123d shape as the result it measures: a build is
   reported from the objects it made, never from values a script planted on
   them. Every execution goes through it, so a saved
   version run again and a numbers-only rebuild pass the same gate as
   generated code, whatever their caller checked. The two halves are one
   layer because neither holds alone: a namespace cannot take back a frame a
   script reaches by attribute, and the gate cannot know which of a module's
   names hold other modules.
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
  loosening the allow-list, or the builtins a script runs with, is a security
  decision, not a convenience patch.
- What a script can reach also moves with build123d. A name a release adds is
  not handed out until it is reviewed, and a test fails until it is; what a
  reviewed name holds can still change underneath it — a new method on a
  reviewed class. The reach walk sees such a method only where it leads to a
  module, a frame, a stream or a shared table the run does not check; one that
  writes a file is seen by the review of the upgrade alone.
- Keeping build123d imports confined to the execution child and the
  exporters is what makes "validate before execute" trustworthy — nothing
  upstream ever runs the untrusted code by accident.
