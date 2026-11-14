# Implementation checkpoint — 2026-11-15

This review branch includes the completed graph planner/executor implementation
and the beginning of the universal gateway update from upstream commit `45e7db2`.
It is a work-in-progress checkpoint for teammate review, not a claim that every
requirement in the new specification is finished.

## Implemented and checked

- HydraDB OSS/MinIO integration, typed response decoding, paginated reads and
  explicit UUID query identities for writes across backend restarts.
- Deterministic 60-tool fixture, graph-derived paths, independent type checking,
  argument binding, schema validation, execution limits and saved plans.
- Real MCP stdio fixture execution, graph learning updates, refusal checks,
  context budgets, structured traces and exact schema-token measurement.
- Optional filesystem and Git adapters, disabled by default. Their tests use
  temporary folders and repositories.
- Reproducible evaluation, benchmark and scale runners; Docker integration CI
  configuration. Hosted CI has not been run for this branch yet.

The retained [full evaluation](../evals/latest.json) used the earlier fixture
interface: 24 tasks, three repetitions, 16 training/8 held-out tasks, five learning
rounds, and real local HydraDB. Graph order validity and task success were 100%;
BM25 order validity and task success were 0%. Held-out accuracy stayed at 100%
throughout training. **There is no measured learning uplift.** These results do
not establish the newer two-tool gateway or OpenCode A/B requirements.

## Universal gateway integration still underway

- Installable `switchboard` CLI and runtime-only connection configuration.
- Exactly two public MCP tools, `find` and `run`, over stdio and optional HTTP.
- Four separately configured fixture servers, namespaced tool keys, discovery
  at indexing/startup, and binding compilation for alternate graph paths.
- Persisting discovery plans and reading them in a separate process for `run`.
- Exact 60-schema versus two-schema measurement and the new 18/6 evaluation split.
- Real OpenCode baseline/gateway A/B runs and client configuration examples.

The current `app/main.py`, static comparison UI and three-tool HTTP interface are
legacy development surfaces from the earlier specification. They are not the
final product described by the new spec. New fixture wrapper files are an initial
checkpoint and still need their integration checks.

## External deliverables

Public deployment, repository visibility changes, a recorded video, slides and
social/submission posts are not completed by this code checkpoint. Draft shipping
material in [SHIP.md](SHIP.md) predates the new gateway direction and needs revision.

No desktop or browser control is used for the remaining implementation work.
