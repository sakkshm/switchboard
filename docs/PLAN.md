# Switchboard build plan

> Backend note (2026-11-15): the hackathon requires the open-source engine.
> Run HydraDB OSS 0.4.0 with Docker Compose (`compose.yaml`: HydraDB + MinIO).
> The managed platform is out of scope. `TESTING.md` has the verified query
> cookbook and traps.

What to build, in what order, and what proves each step is done.
Product rationale is in `SPEC.md`. This file is only the schedule.

Repo: `sakkshm/switchboard`.

---

## 1. The gates

| Gate | Hour | Passes when | If it fails |
|---|---|---|---|
| **G0 Backend** | done | `docker compose up -d --wait` is healthy and `http://127.0.0.1:19090/readyz` returns 200 | Already passed on Nov 14 |
| **G1 Gateway** | H+3 | One fixture upstream added by CLI; `switchboard serve` exposes 2 tools; OpenCode runs a 2-step plan | Fix transport and config before fixtures |
| **G2 Fixture** | H+5 | 60 tools, four servers, six 4-step tasks; `evals/chain_check.py` prints six chains | No ordering claim. Pivot to the eval-harness idea, today |
| **G3 Plan** | H+8 | `switchboard index` discovers 60 tools; `switchboard plan` prints 4 ordered tools; validator rejects a bad chain | Drop to 2 families, one chain, still ships |
| **G4 A/B** | H+11 | Same task in OpenCode: baseline sees 60 tools, gateway sees 2 and executes 4 steps | Cut every P1. Ship counters and the eval table |
| **G5 Learning** | H+15 | 5 rounds, holdout, BM25 vs uniform vs learned, table printed | Report uniform graph and baseline, drop the curve |
| **G6 Ship** | H+18 | Landing page, public repo, Loom, two social posts | Ship what works, state what does not |

G2 is the project. If a four-tool chain does not exist, there is no ordering
product. Do not build the planner first.

---

## 2. Blocks

### B1. Backend probe, done

Verified on Nov 14:

- `compose.yaml`: MinIO on 19000/19001, HydraDB on 18443/17687/19090.
  Healthchecks, named volumes, and a busybox init that writes the auth token
  and chowns the cache volume to uid 10001.
- `.env.example`, `.gitignore`, `pyproject.toml`
- `switchboard/hydra.py`: HTTP client and typed-cell decoder. The Phase 0
  scratch probe that verified the six checks was removed once the gateway
  exercised the same path end to end.
  checks, PASS/FAIL, non-zero exit on failure

The six checks:

1. `GET http://127.0.0.1:19090/readyz` returns 200
2. Node batch with 4 rows; count is 4
3. Edge batch with 3 rows; rerun leaves the count at 3
4. `algo.MSpaths` returns the 4-tool chain in order
5. Plan steps read back with `ORDER BY s.i`
6. `SET r.weight`, read back, and hydrated in the path result

Result: `6/6 PASS`.

Fallbacks, decided now:

- `readyz` fails: check the `hydradb` bucket, `AWS_ENDPOINT=http://minio:9000`,
  and `AWS_VIRTUAL_HOSTED_STYLE_REQUEST=false`
- Writes fail with `PutMode::Update`: `CLOUD_PROVIDER` is `local`. Use MinIO;
  the local filesystem store cannot update
- Volume permission denied: the image runs as 10001, the init service must
  chown `hydradb-cache`
- No path: `MSpaths` omits pairs with no path. Join by endpoint id and raise
  `maxLen` or `pathCount`
- Plan order fails: fall back to `(:Plan)-[:STEP]->(:Step {i})-[:USES]->(:Tool)`.
  This was not needed on 0.4.0

### B2. CLI and gateway skeleton, H+1 to H+3

Build the smallest end-to-end gateway before the 60-tool fixture.

Files:

- `pyproject.toml`: package, console script `switchboard`, deps `mcp`,
  `requests`; dev deps `pytest`, `ruff`
- `switchboard/config.py`: read and write `~/.config/switchboard/config.json`;
  `{env:NAME}` substitution in headers; no secrets written
- `switchboard/cli.py`: `add mcp <name>`, `remove mcp <name>`, `list`, `index`,
  `plan`, `run`, `serve`, `status`
- `switchboard/gateway.py`: `MCPServer` with async `find` and `run`; stdio by
  default, Streamable HTTP with `--http`; logs to stderr only
- `switchboard/hydra.py`: move the verified client and decoder out of the
  removed Phase 0 app stub
- `tests/fixtures/echo_server.py`: two tools, `echo_a` and `echo_b`, with the
  capability header. Throwaway once the real fixture exists

CLI shape:

```bash
switchboard add mcp echo -- python -m tests.fixtures.echo_server
switchboard list
switchboard index
switchboard plan "echo then play it back"
switchboard serve
```

Verify:

```bash
opencode mcp list                         # switchboard connected
opencode run --format json "echo a value, then play it back"
```

The gateway log prints discovered tools, the chosen plan, and the executed
steps. The client must see exactly two tools, `find` and `run`.

Fallbacks:

- MCP tools do not appear at gateway startup: lazy discovery on the first
  `find`, not a startup crash
- stdio transport corrupts: every log line goes to stderr; stdout is protocol
- HTTP transport fails: ship stdio. HTTP is a P1 convenience
- Upstream session cannot stay open: reconnect per `run`, with a clear error

### B3. Fixture and chain check, H+3 to H+5

Build `tests/fixtures/`:

- Four stdio MCP servers, 15 tools each, 60 total:
  `issue_server`, `repo_server`, `incident_server`, `account_server`
- Deterministic, no network, no random values that break bindings. Injectable
  failure via `SWITCHBOARD_FAIL_TOOL` for the evaluator
- Every description ends with one machine-readable line:

```
Read an issue by number. [consumes: repo,issue_number | produces: issue]
```

- `annotations.readOnlyHint` / `destructiveHint` where applicable. The header
  is authoritative for the graph
- Tool results include the fields later steps need, so a real 4-step chain runs

Build `evals/chain_check.py`:

- Connect to each fixture server with a real MCP client
- Parse `consumes` / `produces` from `tools/list`
- For every derived `FEEDS` pair, assert the types actually intersect
- For 6 tasks, assert a 4-step chain exists with a binding between every
  adjacent pair
- Print the chains; exit 1 if fewer than 6

Verify:

```bash
python evals/chain_check.py
pytest tests/unit -q
ruff check .
```

This is G2. Do not proceed if it fails.

### B4. Index and planner, H+5 to H+8

Build:

- `switchboard/matcher.py`: BM25 over tool descriptions, in memory. Returns a
  goal tool and up to 10 candidates. Pure Python, no dependency, no model
- `switchboard/planner.py`: the one `algo.MSpaths` call from `TESTING.md`;
  inline validated candidate keys; score paths by candidate coverage, hop
  penalty, and edge weight; raise `NoPlan` when empty
- `switchboard/validator.py`: every consumed type is produced by an earlier
  step or supplied by the input; return the failing index
- `switchboard/index.py`: `tools/list` from each configured upstream, derive
  `FEEDS`, write nodes in one batch, then edges in another, chunked at 1000.
  Integer ids, label-free `MERGE`, then `SET` the label and key
- `switchboard plan` CLI output: four tool keys in order, with bindings
- `switchboard reset`: `docker compose down -v` then `docker compose up -d`,
  for repeatable runs. Confirm before deleting volumes

Store the plan in HydraDB during `find`:

```cypher
MERGE (p:Plan {id: $id})
SET p.key = $key, p.task = $task, p.hash = $hash, p.wins = 0
MERGE (p)-[:STEP {i: 0, args: $args0}]->(t:Tool {id: $tool0})
```

Verify:

```bash
switchboard index
switchboard index            # idempotent: same counts, exit 0
switchboard plan "comment on the failing test issue in acme/api"
```

prints 4 tools in order. A hand-built bad chain is rejected. Empty graph gives
`NoPlan`. That is G3.

### B5. Executor and MCP surface, H+8 to H+11

Build:

- `switchboard/binder.py`: `$input.*`, `$steps.N.result.*`, and constants only.
  `KeyError` on anything else
- `switchboard/executor.py`: upstream `tools/call`, 10 s timeout, output size
  cap, one JSON log line per step. No shell, no arbitrary SQL
- `gateway.py`: `find` writes and returns the plan; `run` reads the plan,
  validates bindings, executes, and updates `FEEDS.weight`
- Write refusal: if a tool is marked destructive and `--allow-writes` is not
  set, fail the step before calling upstream. P1, cheap
- `switchboard serve --http 127.0.0.1:8765`: Streamable HTTP for multiple
  clients. stdio remains the default

Verify one task end to end. The fixture writes to a local file, the file exists,
and the gateway log shows four upstream calls and the weight updates.

Baseline vs gateway in OpenCode:

| | Baseline | Gateway |
|---|---|---|
| Client config | 4 fixture servers | 1 switchboard server |
| Tools in context | 60 | 2 |
| Calls | model picks tools | `find`, `run` |
| Order source | model | graph path |

This is G4. If it is not true at H+11, cut B6 and B7.

### B6. Evals and learning, H+11 to H+15

Build:

- `evals/tasks.jsonl`: 24 tasks, 4 families, 6 each
- `evals/run.py`: 3 reps, median with spread, writes
  `evals/results/<sha>.json`
- Order-validity checker, shared with the validator
- Three modes:
  - `bm25`: top 4 by description score, no graph order
  - `uniform`: graph order, all weights 1.0
  - `learned`: graph order, weights updated by outcomes
- 5 rounds on a train split, holdout measured after each round
- Static results table for the landing page
- `evals/baseline_tokens.py`: exact tool-schema character and token count from
  `tools/list`, baseline vs gateway. No model call

Verify:

```bash
python evals/run.py --reps 3 --rounds 5
```

prints order validity, task success, schemas, tokens, and graph calls for
baseline, uniform, and learned. Learning line rises on the holdout. If it does
not, the fixture has no ambiguity and the weights have nothing to learn; add
distractors, do not fake the curve.

This is G5.

### B7. HTTP, landing, submission, H+15 to H+18

Build:

- `switchboard serve --http` smoke test with two clients at once
- Static landing page on Vercel: counter table, eval table, Loom embed,
  install commands, architecture, "what is mocked"
- Public repo, README first screen, `LICENSE` if needed
- Loom under 3 minutes, recorded in OpenCode: baseline 60 tools, gateway 2
  tools, `find` plan, `run` results, eval table
- Two social posts tagging HydraDB

No serverless HydraDB, no fake web demo, no `/compare`.

This is G6.

---

## 3. File tree

```
switchboard/
  compose.yaml          MinIO + HydraDB
  pyproject.toml        console script: switchboard
  switchboard/
    cli.py              add, remove, list, index, plan, run, serve, status
    config.py           ~/.config/switchboard/config.json
    hydra.py            HTTP client and typed-cell decoder
    index.py            tools/list -> graph
    matcher.py          BM25
    planner.py          one MSpaths call
    validator.py        order check
    binder.py           $input / $steps
    executor.py         upstream calls, timeouts, caps
    gateway.py          MCPServer, find and run
  evals/
    tasks.jsonl
    chain_check.py
    run.py
    baseline_tokens.py
    results/
  tests/
    unit/
    integration/
    fixtures/
      common.py         tool table, header parser, result helpers
      echo_server.py    G1 two-step chain
      issue_server.py
      repo_server.py
      incident_server.py
      account_server.py
  docs/
  switchboard.example.json
  .env.example
  README.md
```

If the tree grows past forty files, something is being built that the demo does
not need.

---

## 4. Golden set

Four families, six tasks each. One write per family, at the end.

| Family | Chain |
|---|---|
| Issue triage | `repo_search` -> `list_issues` -> `get_issue` -> `add_comment` |
| Code review | `locate_file` -> `read_file` -> `get_diff` -> `post_review` |
| Incident | `search_logs` -> `get_trace` -> `open_ticket` -> `notify_channel` |
| Account | `find_account` -> `list_orders` -> `get_order` -> `issue_refund` |

The six G2 tasks are the four canonical chains plus two variants:

| Variant | Chain |
|---|---|
| Close an issue | `list_issues` -> `get_issue` -> `list_comments` -> `close_issue` |
| Page on-call | `search_logs` -> `get_trace` -> `find_oncall` -> `page_oncall` |

The variants share a prefix with a canonical chain and end in a different
terminal tool. That gives the planner a real choice and the weights something
to learn.

Adversarial, 6 if time: ungranted write, destructive without `--allow-writes`,
unknown tool, no matching plan, upstream timeout, oversized output.

Each task record: `task`, `expected_chain`, `expected_values`,
`allowed_extras`, `expected_end_state`.

---

## 5. Cut list, decided in advance

| At | If | Cut |
|---|---|---|
| H+5 | G2 fails | the project. Switch ideas, not features |
| H+11 | no working A/B | eval UI, HTTP daemon, write refusal, reuse. Keep counters and one chain |
| H+15 | no eval table | learned curve. Keep BM25 and uniform graph, state the result honestly |
| H+16 | behind | adversarial set, HTTP, plan reuse |
| H+18 | anything still broken | stop coding. Record what works |

Never cut: the removal test, the eval numbers, the BM25 baseline, honesty about
what is mocked.

---

## 6. Submission checklist

- Public repo, README first screen says what it does and shows the counter table
- Removal test visible: empty graph means `find` returns `NoPlan`
- Eval table with n, reps, and the baseline
- Video under 3 minutes, unlisted
- 5 slides: problem, counters, order validity gap, GTM, next
- Form: name, description, how HydraDB is used (OSS traversal + learned edge
  weights), repo, video
- State plainly: fixtures are ours, 60 tools are synthetic, capability headers
  are a convention, no auth, no live gateway
- Track 2: agent context layer, not a hosted app

---

## 7. Day-of commands

```bash
cp .env.example .env
docker compose up -d --wait                # G0
./install.sh                               # venv + `switchboard` on PATH
.venv/bin/python evals/chain_check.py      # G2
switchboard add mcp issues -- python -m tests.fixtures.issue_server
switchboard add mcp repo -- python -m tests.fixtures.repo_server
switchboard add mcp incident -- python -m tests.fixtures.incident_server
switchboard add mcp account -- python -m tests.fixtures.account_server
switchboard index                # G3
switchboard plan "comment on the failing test issue in acme/api"
switchboard serve                # OpenCode launches this
.venv/bin/python evals/run.py --reps 3 --rounds 5    # G5
```

Baseline OpenCode config lives in `evals/harness/baseline/` and points at the
four fixture servers directly. The gateway config lives in
`evals/harness/gateway/` and points at `switchboard serve`. Run both from their
own directories so configs do not overlap.
