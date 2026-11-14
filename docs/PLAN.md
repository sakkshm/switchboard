# Switchboard build plan

> Backend note (2026-11-14): the hackathon requires the open-source engine.
> Run it with Docker Compose (`compose.yaml`: HydraDB OSS 0.4.0 + MinIO).
> The managed platform is out of scope; OSS steps are in B1 and B3.
> `TESTING.md` has the verified query cookbook and traps.

What to build, in what order, and what proves each step is done.
Product rationale is in `SPEC.md`. This file is only the schedule.

Hours are H+0 from whenever you start. If you are already at H+6, jump to the
next gate and skip the prose.

Repo: `hydradb-hack/switchboard`.

---

## 1. The five gates

| Gate | Hour | Passes when | If it fails |
|---|---|---|---|
| **G0 Backend** | H+1 | `docker compose up -d` is healthy and `scripts/probe.py` prints 6/6 PASS | Fix compose, MinIO, or the S3 env before writing app code |
| **G1 Chain** | H+4 | 6 tasks have a 4-tool chain with 3 bindings, proven by `evals/chain_check.py` | No ordering claim. Pivot to the eval-harness idea, today |
| **G2 Path** | H+6 | One `algo.MSpaths` call returns a 4-tool chain from seeded data | Fix the query, not the product. Two hours max |
| **G3 Plan** | H+10 | `/plan` returns 4 ordered tools for 6 tasks, validator passes | Drop to 2 families, one chain, still ships |
| **G4 Demo** | H+13 | `/compare` shows 60 tools vs 4, and the right column executes with a visible side effect | Cut every P1. Ship counters and the eval table |

G1 is the project. Everything after it is execution.

---

## 2. Blocks

### B1. Backend probe, H+0 to H+1

Build:

- `compose.yaml`: `minio`, `hydradb`, `switchboard`. MinIO on 19000/19001,
  HydraDB on 18443/17687/19090, app on 18000 (8000 is taken on this machine).
  Healthchecks and `depends_on`. The busybox init writes the auth token and
  chowns the cache volume, because HydraDB runs as uid 10001.
- `Dockerfile`, `.dockerignore`, `.env.example`, `.gitignore`, `requirements.txt`
  (`fastapi`, `uvicorn`, `requests`, `mcp`, `pytest`, `ruff`)
- `app/store.py`: the HTTP client and typed-cell decoder; `scripts/probe.py`:
  six checks, printed PASS/FAIL, non-zero exit on any failure

The six checks, in this order:

1. `GET http://127.0.0.1:19090/readyz` returns 200
2. `UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Tool, n.key = row.key, n.name = row.name` with 4 rows; count is 4
3. Edge batch with 3 rows; rerun leaves the count at 3
4. `algo.MSpaths` returns the 4-tool chain in order
5. `MERGE (p:Plan {id: 100})-[:STEP {i: 0}]->(t:Tool {id: 1})`; read back with `ORDER BY s.i`
6. `SET r.weight = $w`, read it back, and see it hydrated in the path result

Check 4 tests the planner query shape. Check 5 tests reading a plan back.
Check 6 tests the learning signal.

Verify: `docker compose up -d` is healthy, then `python scripts/probe.py` exits 0
with `6/6`.

Fallbacks, decided now so you do not deliberate later:

- `readyz` fails: check the `hydradb` bucket, `AWS_ENDPOINT=http://minio:9000`,
  and `AWS_VIRTUAL_HOSTED_STYLE_REQUEST=false`
- Writes fail with `PutMode::Update`: `CLOUD_PROVIDER` is `local`. Use the
  MinIO-backed service; the local filesystem store cannot update
- No path: `MSpaths` omits pairs with no path. Join by endpoint id and raise
  `maxLen` or `pathCount`
- Plan order fails: fall back to `(:Plan)-[:STEP]->(:Step {i})-[:USES]->(:Tool)`.
  This was not needed on 0.4.0

### B2. Fixture catalog and the chain check, H+1 to H+4

Build `catalog/fixture_server.py`: an MCP stdio server, 60 tools, 4 families of 15. Deterministic, no network, injectable failures via env var.

MCP does not declare outputs. Encode capability in one line at the end of each description, machine-readable:

```
Read an issue by number. [consumes: repo,issue_number | produces: issue]
```

Plus `annotations.readOnlyHint` / `destructiveHint` where the client sends them; treat the description header as authoritative.

Write `evals/chain_check.py`:

- for each tool: `consumes` must be non-empty or the tool must be an entry point
- for each `FEEDS` pair derived from the catalog: assert the types actually intersect
- for 6 chosen tasks: assert a 4-step chain exists with a binding between every adjacent pair
- print the chains, exit 1 if fewer than 6

Verify: `python evals/chain_check.py` prints 6 chains of length >= 4.

This is G1. Do not proceed if it fails.

### B3. Seed, H+4 to H+6

Build:

- `scripts/seed.py`: `tools/list` from each server, parse the capability header, derive `FEEDS` cross-server, write nodes in one batch (integer ids, label-free `MERGE`, then `SET` the label and key), then edges in another, chunked at 1000
- write `catalog/catalog.json` as a committed snapshot, so tests do not depend on a live server
- `scripts/reset.py`: `docker compose down -v` then `docker compose up -d`, for a clean store and repeatable runs

Verify:

```
python scripts/seed.py
python scripts/seed.py            # idempotent: same counts, exit 0
```

and one query returning a 4-chain. That is G2.

### B4. Planner, H+6 to H+10

Build `app/matcher.py`, `app/planner.py`, `app/validate.py`:

- matcher: BM25 over `catalog.json` descriptions, in memory. Returns goal tool + up to 10 candidates. Pure Python, no dependency, no model.
- planner: the one `algo.MSpaths` call from `TESTING.md` section 4; inline validated candidate keys; score paths by candidate coverage minus 0.1 per hop; `NoPlan` when empty
- validate: every consumed type is produced by an earlier step or supplied by input; return the failing index when not

Verify:

```
pytest tests/unit -q
python scripts/plan_cli.py "comment on the failing test issue in acme/api"
```

prints 4 tools in order, and `pytest tests/unit/test_order.py` passes with a hand-built bad chain rejected.

That is G3.

### B5. Executor, binder, grants, H+10 to H+13

Build:

- `app/binder.py`: three sources, five ops, `KeyError` on anything else
- `app/executor.py`: `tools/call` with a 10 s timeout, grant re-check before each call, output size cap, audit node per step
- `app/grants.py`: `GRANTED` edges per agent, `read` / `write` / `destructive`, confirmation token for destructive
- `app/main.py`: `POST /compare`, `POST /run/{plan_id}`, `GET /trace/{id}`, `GET /metrics`, `GET /catalog`
- `static/index.html`: two columns, counters, chain strip, result box, read-only toggle

Verify: run one task end to end. The fixture writes to a local file. The file exists. Counters show `tools=60` on the left, `tools=4` on the right.

That is G4. If this is not true at H+13, cut B6 and B7.

### B6. Evals, H+13 to H+16

Build:

- `evals/tasks.jsonl`: 24 tasks, 4 families, 6 each (below)
- `evals/run.py`: 3 reps, median with spread, writes `evals/results/<sha>.json`
- order-validity checker, shared with the planner validator
- `--baseline bm25` flag: same candidates in BM25 rank order, no graph
- `static/evals.html`: one row per family, baseline and Switchboard

Verify: `python evals/run.py --reps 3` prints a table with order validity for both columns. Switchboard 1.00, baseline under 0.75. If the gap is small, the fixture chains are too easy and you need harder ones, not a better pitch.

### B7. Reuse, block, trace, H+16 to H+18

Build: plan persistence, `intent_hash` lookup, `wins` increment, second-run counters, 8 adversarial tasks, `/trace/{id}` page, JSON logs.

Verify: run the same task twice in the UI. Graph calls go 1 to 0. Read-only toggle refuses the write step and the chain strip turns red.

### B8. One of two, H+18 to H+20

Pick **scale test** first. `scripts/scale.py` seeds 10,000 tools and 100,000 extra edges into the graph, reruns the planner benchmark, and prints p50/p95 next to the 60-tool numbers. Labelled synthetic.

Step memoization only if the scale test is done and the video is not recorded. Box it at 45 minutes. Delete on overrun.

### B9. Video and slides, H+20 to H+21

Record at H+20. Not H+23. Beats: counters, run, chain, blocked write, second run, eval table. Three minutes, phone mic is fine, no music.

### B10. Ship, H+21 to H+24

README, removal test, eval table, "what is mocked", submission form. Then stop.

---

## 3. File tree

```
switchboard/
  compose.yaml       MinIO + HydraDB + app
  Dockerfile
  .dockerignore
  .gitignore
  app/
    main.py          routes
    store.py         HydraGraph client, 6 functions
    matcher.py       BM25
    planner.py       one MSpaths call
    validate.py      order check
    binder.py        $input / $steps
    executor.py      calls, grants, audit
    grants.py
    metrics.py
    trace.py
  catalog/
    fixture_server.py
    servers.yaml
    catalog.json     committed snapshot
  evals/
    tasks.jsonl
    chain_check.py
    run.py
    results/
  static/{index,evals,catalog}.html
  scripts/{probe,seed,reset,plan_cli,scale}.py
  tests/{unit,contract,integration,e2e}/
  .env.example
  Makefile
  README.md
```

If the tree grows past forty files, something is being built that the demo does not need.

---

## 4. Golden set

Four families, six tasks each. One write per family, at the end.

| Family | Chain |
|---|---|
| Issue triage | `repo_search` -> `list_issues` -> `get_issue` -> `add_comment` |
| Code review | `locate_file` -> `read_file` -> `get_diff` -> `post_review` |
| Incident | `search_logs` -> `get_trace` -> `open_ticket` -> `notify_channel` |
| Account | `find_account` -> `list_orders` -> `get_order` -> `issue_refund` |

Adversarial, 8: ungranted write, destructive without token, unknown tool, injection text inside a tool description, stale plan after a version bump, no matching plan, upstream timeout, oversized output.

Each task record: `task`, `expected_chain`, `expected_values`, `allowed_extras`, `expected_end_state`.

---

## 5. Cut list, decided in advance

| At | If | Cut |
|---|---|---|
| H+4 | G1 fails | the project. Switch ideas, not features |
| H+13 | no working `/compare` | eval UI, trace page, memoization, scale test. Keep counters and one chain |
| H+16 | no eval table | the LLM baseline. Keep the BM25 baseline, it is one flag |
| H+18 | behind | adversarial set to 3 tasks, trace page gone, memoization gone |
| H+20 | anything still broken | stop coding. Record what works |

Never cut: the removal test, the eval numbers, the blocked write, honesty about what is mocked.

---

## 6. Submission checklist

- Public repo, README first screen says what it does and shows the counter table
- Removal test visible: empty graph means `plan()` raises `NoPlan`
- Eval table with n, reps, and the baseline
- Video under 3 minutes, unlisted
- 5 slides: problem, counters, order validity gap, GTM, next
- Form: name, description, how HydraDB is used (OSS traversal + learned edge weights), repo, video
- State plainly: fixture server is ours, 60 tools are synthetic, scale test is synthetic, no auth

---

## 7. Day-of commands

```bash
cp .env.example .env
docker compose up -d --build
pip install -r requirements.txt
python scripts/probe.py        # G0
python evals/chain_check.py    # G1
python scripts/seed.py         # G2
# app: http://localhost:18000
python evals/run.py --reps 3
make bench
```
