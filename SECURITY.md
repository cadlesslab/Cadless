# Security Policy

Cadless executes code that a language model just wrote. Making that safe is the
core engineering problem of the project, not a side concern — so reports about
the execution boundary are the ones we care about most.

## Supported versions

Cadless is pre-1.0 and there is no backport channel. Fixes land on `main`, and
`main` is the only supported version. If you are running an older tag, update
before reporting.

## Reporting a vulnerability

**Do not open a public issue for a security report.**

Use GitHub's private vulnerability reporting: go to the
[Security tab](https://github.com/cadlesslab/Cadless/security) and click
**Report a vulnerability**. That opens a private advisory visible only to you
and the maintainers.

A useful report includes:

- what an attacker gains — arbitrary code on the host, a key, a file they
  should not reach;
- the exact input: the prompt, the generated or catalog step code, or the HTTP
  request;
- which layer you got past (see the scope table below);
- the commit you tested and how you were running the stack (`docker compose`
  or a local process).

What to expect:

| Stage | Target |
|---|---|
| Acknowledgement | 5 business days |
| Initial assessment, with a scope decision | 10 business days |
| Fix on `main` and a published advisory | negotiated with you, based on severity |

This is a small project. If a deadline slips, the advisory thread is where we
say so.

## Scope

Cadless's isolation model is written down in
[ADR-0003](./docs/adr/0003-three-layer-sandbox.md) (three sandbox layers, no
kernel isolation) and [ADR-0004](./docs/adr/0004-local-first.md) (local-first,
loopback only, bring-your-own-key). Those ADRs decide what counts as a
vulnerability: breaking a boundary we claim is in scope, and exploiting a
limit we documented as absent is not.

### In scope

- **Static gate bypass** — any input that gets past `validate_code` in
  `cadless/validation.py` and the allow-list in `cadless/api_subset.py` to run
  Python outside the permitted subset. The AST gate is a security boundary;
  loosening it is a security decision.
- **Execution escape** — breaking out of the worker subprocess or its
  container: reaching the network from the internal-only worker network,
  writing outside the paths the worker is meant to write, escalating past the
  non-root container user, or defeating the CPU and wall-clock limits in a way
  that reaches the host.
- **Malicious catalog content** — a catalog item that achieves any of the
  above. Imported catalog step code is untrusted input and passes the same
  gate as generated code.
- **Secret exposure** — an API key from the settings store reaching logs,
  exported artifacts, the browser, or any destination other than the provider
  call it was entered for.
- **Path traversal or arbitrary read/write** through the HTTP API, the artifact
  exporters, or the catalog loader.
- **Proxy scope violation** — the frontend proxy serving or forwarding
  anything outside its own path prefix.

### Out of scope

These are documented design decisions. Reporting them tells us something we
already published, so they will be closed as out of scope:

- **Container escape that requires a host kernel vulnerability.** ADR-0003
  evaluated gVisor and microVM isolation and deliberately did not adopt them.
  There is no fourth layer, and the README says so.
- **The unauthenticated settings endpoint.** It stores API keys without auth
  because the published port binds `127.0.0.1` and the tool is single-user.
  Exposing the stack beyond loopback is what breaks this, and ADR-0004 states
  that anyone doing so must add authentication and kernel-level isolation
  first.
- **Multi-tenant or internet-facing deployment.** Cadless as shipped is not
  hardened for it, and single-user assumptions are baked into the settings
  store and the database.
- **Resource exhaustion contained by the worker's own cgroup limits** — a
  prompt that makes your own machine work hard is not a vulnerability.
- **Vulnerabilities in dependencies** such as build123d or OCCT. Report those
  upstream. We do want to hear about it if Cadless's usage turns an upstream
  issue into something worse, or if we can mitigate it here.
- **Bad CAD output.** Wrong geometry, failed repair loops, and unhelpful model
  responses are correctness bugs — open a normal issue.

## Disclosure

We work with you on a coordinated disclosure. Once a fix is on `main` we
publish a GitHub Security Advisory describing the issue and the affected
commits. Reporters are credited by name unless you ask us not to.

## If you are deploying Cadless for other people

The default configuration is not the one you want. Read ADR-0003 and ADR-0004
first: you need kernel-level isolation for the worker and authentication in
front of the settings endpoint before anyone but you can reach the stack.
