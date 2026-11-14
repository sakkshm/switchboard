# Switchboard

An MCP gateway where the agent asks for a task, not a catalog.

Status: spec, rewritten after the BYOG discovery. Parallel load-bearing claims
are marked PROBE until measured.

> Backend note (2026-11-14): the hackathon requires the OSS engine, not managed
> BYOG. Sections on `/byog/*`, `client.query()`, and "no Docker" describe the
> managed variant and are not the submission backend. See `TESTING.md`.

---

## 0. The correction that changes the design

My earlier spec said the managed API cannot hold a custom graph, so the tool
dependency edges would have to be metadata and we would have to self-host the
Rust engine in Docker.

That was wrong.

HydraDB Cloud exposes **Bring Your Own Graph (BYOG)**:

| Endpoint | What it does |
|---|---|
| `POST /byog/databases` | create a graph database, ready immediately |
| `POST /byog/query` | run Cypher: `CREATE`, `MATCH`, `MERGE`, `SET`, `DELETE`, `UNWIND`, `WITH`, `CALL { }` |
| `GET /byog/collections` | list collections |
| `DELETE /byog/collections` | drop a collection |
| `DELETE /byog/databases` | drop a database |

The docs state that all of openCypher is supported except server-side
procedures (`apoc.*`, `db.*`) and `LOAD CSV`. Multi-hop patterns, bounded and
unbounded variable-length traversal, `shortestPath`, `DISTINCT`, aggregation,
and `ORDER BY` are all listed as supported. Results come back as plain row
dicts in `data`, not the typed-cell format of the self-hosted engine.

Consequences:

1. **No Docker, no Rust build, no compose file.** The single largest execution
   risk is gone.
2. **Real edges.** `(:Tool)-[:FEEDS]->(:Tool)` is traversed, not faked as a
   string property. Ordering becomes an actual path in the graph.
3. **No async indexing.** `POST /byog/query` is synchronous. No polling, no
   `graph_creation` state, no 1 to 5 minute waits, no flaky demo.
4. **No cell decoder.** Rows are dicts. The `vertex_id` / `cell_id` handling in
   the old spec is deleted.
5. **Plans are graph objects.** `(:Plan)-[:STEP {i}]->(:Tool)` is read back as a
   path. Reuse is a traversal.

The whole product now runs against `api.hydradb.com` with one API key.

---

## 1. Problem

An LLM has no hands. Tools give it hands, but every tool it might use is
described in its context on every turn, and it selects by similarity, not by
correctness.

Four costs, in the order they hurt:

1. **Bloat.** 60 tools at roughly 120 tokens each is about 7,000 tokens per turn
   to use maybe 4.
2. **Wrong order.** Tool B needs an ID tool A produces. The model finds out by
   trying, failing, and retrying. Every retry is a round trip.
3. **No memory.** The sequence dies with the session and is rediscovered next
   time.
4. **No boundary.** The model is handed the whole catalog, including the tools
   that delete things.

## 2. Product

Switchboard sits between one agent and N MCP servers.

- It holds the catalog, so the agent never does.
- It returns a plan: the tools that chain, in order, plus the argument values
  that worked last time.
- It executes the plan itself, binding arguments between steps.
- It decides what the agent may call, before execution, not after.

One line: the agent never loads 60 tools. It asks for a task and gets a path
through the tool graph, plus a proxy that runs it.

Non-goals: entity resolution, dedupe, cross-source identity, memory products,
agent frameworks, vector databases, auth, deployment.

The catalog is the index, not the payload. Section 23 is the answer to "60 tools
is too small to need HydraDB."

## 3. Why the graph is load-bearing

Order is not a model decision and not a sort in Python. It is a path.

- A tool declares the types it `CONSUMES` and `PRODUCES`.
- Seeding derives `FEEDS`: A feeds B when `A.produces` intersects `B.consumes`.
- The planner asks the graph for a path ending at the goal tool. The returned
  node order **is** the plan order.
- A stored plan is a graph path too, so reuse is a traversal, not a cache hit.

Two independent checks agree, and both are cheap:

| Check | Source | Fails when |
|---|---|---|
| Path validity | `MATCH p = (a)-[:FEEDS*1..6]->(goal)` | the edge set is wrong or missing |
| Type validity | `consumes` subset of `produces` so far | the edge derivation is wrong |

Removal test: with the graph empty, `plan()` raises `NoPlan`. There is no
fallback catalog and no local ordering heuristic. That is a pytest, not a
paragraph.

## 4. The one query

Everything the planner needs from HydraDB is one Cypher call.

```cypher
MATCH p = (entry:Tool)-[:FEEDS*1..6]->(goal:Tool {key: $goal})
WHERE entry.key IN $candidates
RETURN [n IN nodes(p) | n.key]  AS chain,
       [n IN nodes(p) | n.name] AS names,
       length(p)                AS hops
ORDER BY hops ASC
LIMIT 25
```

Ten candidate entry points, one goal, six hops maximum. The graph returns every
path that ends at the goal; Python scores them by coverage of the candidate set
and picks one. No topological sort is written by hand.

PROBE: that `WHERE` on the first node of a variable-length pattern is accepted,
and that `nodes(p)` returns tools only. If either fails, the fallback is
`MATCH (a:Tool)-[:FEEDS*1..6]->(g:Tool {key:$goal}) RETURN a.key, collect(g.key)`
plus client-side filtering, or a plain edge fetch and a local chain walk. The
graph stays load-bearing either way; only the amount of Cypher changes.

Matching the task text to a goal tool does **not** go to HydraDB. A BM25 index
over the 60 catalog descriptions, built in memory at startup, is 20 lines, has
zero model calls, and is exact for the fixture. Sending it to the graph would be
a full scan of a text property with no index. Keep it local and say why.

So: one graph query per plan, zero on a reused plan. That is the number the
metrics page shows.

## 5. Architecture

```
MCP client (opencode, Claude, any agent)
    |  find / describe / run
    v
Switchboard (FastAPI + MCP server, one process)
    |-- matcher    BM25 over catalog.json snapshot -> goal + candidates
    |-- planner    one /byog/query -> ordered path
    |-- validator  consumes/produces check on the returned path
    |-- binder     $input / $steps.N.result -> tool args
    |-- executor   MCP tools/call, grants, timeouts, audit
    +-- trace      /trace/{id}, /metrics, /evals
    |
    v
HydraDB Cloud  POST /byog/query
    ^
    |  MERGE seeding from each server's tools/list
    |
N MCP servers (fixture, filesystem, git)
```

No LLM on the default path. No vector store. No graph visualisation library.

## 6. Gateway surface

Exactly three MCP tools. The agent never sees the real catalog.

| Tool | Input | Returns |
|---|---|---|
| `find` | task text | matching tool names and one-line descriptions |
| `describe` | tool name | that one tool's JSON schema |
| `run` | plan id, input args | executes the plan through the proxy |

`find` and `describe` exist so the agent can improvise when no plan matches,
without ever loading the catalog.

## 7. Graph schema

```
(:Tool     {key, name, server, desc, writes, schema})
(:Type     {name})
(:Plan     {key, intent, intent_hash, values, wins})
(:Agent    {key, name})
(:Decision {key, ts, agent, tool, verdict, reason, args_hash})

(:Tool)-[:CONSUMES]->(:Type)
(:Tool)-[:PRODUCES]->(:Type)
(:Tool)-[:FEEDS]->(:Tool)
(:Plan)-[:STEP {i}]->(:Tool)
(:Agent)-[:GRANTED]->(:Tool)
(:Plan)-[:BY]->(:Agent)
```

`key` is the upsert key everywhere; `id` is internal and is never used by the
app. One label per node. `FEEDS` is derived at seed time, cross-server, because
the point is chaining across servers.

`STEP` is a star (plan to each tool) with the index on the relationship, so
`MATCH (:Plan {key:$k})-[s:STEP]->(t:Tool) RETURN t.key ORDER BY s.i` reads the
plan back in order. PROBE: ordering by a relationship property. Fallback: chain
the steps as `(:Plan)-[:STEP]->(:Step {i})-[:USES]->(:Tool)`.

Optional and last: an index on `Tool.key`, and on `Plan.intent_hash` for reuse.

## 8. Planner, end to end

1. Task text in, principal in.
2. BM25 over catalog descriptions. Take a goal tool and up to 10 candidates.
   Also take the stored `values` for the best matching plan, if any.
3. If a plan with a close `intent_hash` exists, load its steps by traversal and
   skip to 5.
4. One Cypher call. Score paths by candidate coverage minus a length penalty.
   No path means `NoPlan`.
5. Validate types: for each step, every consumed type must already be produced
   by an earlier step or supplied by the input.
6. Bind arguments, check grants, execute, audit.
7. On success, write the plan node and its `STEP` edges. Idempotent by key.

## 9. Plan format and binding

```json
{
  "key": "plan_issue_comment",
  "intent": "comment on the failing test issue",
  "steps": [
    {"i": 0, "tool": "repo_search",  "args": {"query": "$input.repo"}},
    {"i": 1, "tool": "list_issues",  "args": {"repo": "$steps.0.result.full_name", "state": "open"}},
    {"i": 2, "tool": "get_issue",    "args": {"repo": "$steps.0.result.full_name", "number": "$steps.1.result.0.number"}},
    {"i": 3, "tool": "add_comment",  "args": {"repo": "$steps.0.result.full_name", "number": "$steps.2.result.number", "body": "$input.message"}}
  ],
  "values": {"repo": "acme/api"},
  "wins": 0
}
```

Three binding sources only: `$input.*`, `$steps.N.result.*`, and constants.
Five ops only: `str`, `int`, `join`, `pick`, `default`. Anything else means the
plan is wrong and must fail loudly, not improvise.

This is why the gateway executes. If the agent executes, the binding lives in
the model's head and order can drift. If the plan executes, the binding is data
and order cannot drift.

## 10. Reuse

A successful run writes `(:Plan)` plus `STEP` edges and increments `wins` on
subsequent hits. Second run of the same task:

| Counter | Run 1 | Run 2 |
|---|---|---|
| graph queries | 1 | 0 |
| tools considered | 60 | 0 (plan hit) |
| tools executed | 4 | 4 |
| model calls | 0 | 0 |

That table is the second demo beat, and it is measurable, not asserted.

## 11. Safety, control, identity

Principal is `{agent_key, user}`. From a header in the demo, from config in a
real deployment. It is a stub and the README says so.

Enforcement at three points, not one:

1. `plan()` filters candidates by `GRANTED`. An agent never sees a tool it
   cannot call.
2. `executor()` re-checks before every call. The plan may be stale or forged.
3. `audit()` writes a `:Decision` node with verdict and reason. Append-only.

Risk classes: `read`, `write`, `destructive`.

- `destructive` needs a confirmation token in the request.
- A refusal during discovery returns no plan, not "blocked", so the gateway does
  not leak that a tool exists. The refusal is still logged.
- Rate limit: token bucket per principal, 30/min, in memory.
- Arguments validated against the tool's JSON Schema. Unknown fields rejected.
- Server allowlist in config. No shell tool, no arbitrary SQL.
- Tool output capped in size, control characters stripped.

## 12. Observability

| Piece | Implementation | Demo value |
|---|---|---|
| Logs | one JSON line per stage: `request_id`, `stage`, `ms`, `verdict` | grep-able in the video |
| Trace | in memory, plus `:Decision` nodes, `GET /trace/{id}` | the result box is a trace |
| Metrics | `/metrics`, Prometheus text, hand-rolled, 30 lines | counters on screen |
| Cost | tokens, graph calls, tool calls, model calls per task | one number per column |
| Audit | append-only `:Decision {ts, agent, tool, verdict, reason, args_hash}` | "who did what" is queryable |

No collector, no Jaeger, no dashboard stack.

## 13. Performance

Budgets per stage: plan 50 ms, graph query 100 ms, tool call wherever the
server lands. Log `budget_exceeded`, do not fail the request.

Report p50, p95, p99 per stage and end to end. Never a single timing.

Load: 20 concurrent `/compare`, semaphore of 8 upstream, report p95 and error
rate. The cloud round trip is the floor here; measure it, do not guess.

`make bench` writes `results/bench-<sha>.json`:

- 1,000 planner iterations on a fixed task, no execution
- 24 golden tasks times 3 reps
- one load run

The file is diffable across commits, which is the whole point.

BYOG limits that shape the code:

| Limit | Value | Response |
|---|---|---|
| Request body | 256 KiB | chunk seeds at 500 rows |
| Read budget | 8 s | keep the planner query under 6 hops, index `Tool.key` |
| Write budget | 30 s | seeds are small, not a risk |
| Result cap | deployment value, silently truncated | always `LIMIT` |

## 14. Evals

Golden set: 24 tasks, 4 families, 6 each. Fields: task, expected ordered tools,
expected input values, allowed extras, expected end state.

| Metric | Target |
|---|---|
| Tool precision | >= 0.90 |
| Tool recall | 1.00 on P0 families |
| **Order validity** | **1.00** |
| Baseline order validity | measure, expect 0.55 to 0.70 |
| Task success | >= 0.85 |
| Model-call-free rate | >= 0.85 |
| Reuse hit rate | >= 0.80 on runs 2 and 3 |
| Block rate | 8 of 8 adversarial tasks |
| p95 assembly | < 400 ms |
| Timeout behaviour | clean error under 10 s |

Order validity is structural: every step consumes a type produced by an earlier
step, or supplied by the input. No LLM judge, no rubric, no disagreement.

Baseline is the same candidate tools in BM25 rank order, with no graph. Same
executor, same golden set, one flag. That isolates the graph and nothing else.

### Ablation

| Variant | Order validity | Graph calls | Success |
|---|---|---|---|
| Full | 1.00 | 1 (0 on reuse) | 0.90 |
| BM25 order, no graph | ~0.6 | 0 | ~0.6 |
| No stored values | 1.00 | 1 | ~0.8 |
| No permission filter | 1.00 | 1 | same, 8 blocks missed |
| LLM picks the order | measure | 1 + n calls | measure |

The last row is optional and runs offline (`scripts/baseline_llm.py`, gated by
`GEMINI_API_KEY`). It never sits in the demo path, so the demo never depends on
a model being up.

Honesty rules: 3 reps per task, median with spread, same catalog snapshot, state
n on the slide. If success ties with the baseline, lead with cost and order
validity.

## 15. Testing

| Layer | Covers |
|---|---|
| Unit | type derivation, path scorer, order validator, binder, token counter |
| Contract | MCP client against recorded `tools/list` fixtures |
| Integration | seed, plan, execute against a scratch collection in the cloud |
| E2E | one task per family, end state asserted |
| Golden | `catalog.json` and `ontology.json` snapshots, drift visible in diffs |
| Removal | empty collection means `plan()` raises `NoPlan` |
| Isotonic | same task planned twice returns the same order |

CI: ruff and pytest on push. Bench is manual; it costs minutes and quota.
Integration tests run against a `switchboard_test` database and skip cleanly
when `HYDRADB_API_KEY` is unset.

## 16. UI

Three routes, one nav, no build step, no framework.

### `/` comparison

1. Task input, three preset chips, `Run`, `read-only` toggle, `Second run`.
2. Two columns: `Preload all` and `Switchboard`.
3. Counters per column: tools, tokens, graph calls, tool calls, wall time.
4. Tool names actually loaded, so the gap is visible.
5. Result box with the executed trace.
6. Chain strip: `Repo -> Issue -> Comment -> Slack`. A blocked step turns red.
7. Second run shows reuse: graph calls fall to zero, latency falls.

The left column is labelled **static cost, measured**: the token count of the
full catalog is exact and computed, not simulated. Its order-validity number
comes from the eval run, not from a live agent. Say so on screen.

### `/evals`

Golden set results, one row per family, baseline and Switchboard. Columns:
precision, recall, order validity, success, tokens, graph calls. This is the
engineering slide, rendered.

### `/catalog`

All tools, searchable, with server, consumes, produces, write flag, and the
number of saved plans that use it (a real graph query, not a counter cache).

## 17. Demo

Three minutes, counters first.

| Time | Beat |
|---|---|
| 0:00 | 60 tools, one task. Watch the counters. |
| 0:20 | Run. Left loads 60, right loads 4. |
| 0:50 | Show the chain, and one tool from the catalog that cannot consume the previous step. Same task, no path. |
| 1:20 | `read-only` toggle, run, write refused at the executor. |
| 1:50 | Second run. Graph calls 1 to 0. Plan reused. |
| 2:20 | `/evals`: order validity baseline vs Switchboard. |

60 second cut: beats 1, 2, 5, 6. Record at hour 20.

## 18. Features

P0, must demo by hour 12:

- `probe.py`: six checks against the cloud, fails loudly
- Catalog ingest from `tools/list`, with `consumes` / `produces` / `writes`
- `FEEDS` derivation and idempotent seeding
- `plan()` returning an ordered path in one query
- Type validator
- Binding and proxy execution
- Grant filtering
- Counters and `/compare`
- Golden set and order-validity checker

P1, if P0 is clean by hour 20:

- Plan persistence, reuse, second-run counters
- `find` and `describe` gateway tools
- Ablation table
- Audit nodes and `/trace/{id}`
- `scripts/baseline_llm.py`

P2, only if everything else is done:

- Cost estimate per task
- Second persona
- `LocalStore` behind the same interface

Never: auth, SSO, deploy config, Kubernetes, graph visualisation, fine-tuning.

## 19. Milestones

The schedule, gates, and cut list live in `PLAN.md`. Gates are G0 probe (H+1),
G1 chain (H+4), G2 path (H+6), G3 plan (H+10), G4 demo (H+13).

G1 is the real decision point. If a four-tool chain does not exist in the
fixture catalog, the project has no ordering claim and becomes tool RAG.

## 20. Setup

```bash
cp .env.example .env        # HYDRADB_API_KEY
pip install -r requirements.txt
python scripts/probe.py     # six checks, fails loudly
python scripts/seed.py      # idempotent, safe to rerun
make dev                    # app on :8000
make demo                   # one comparison run, headless
make bench                  # writes results/
```

No Docker. No compose. No local engine.

SDK coverage, verified against the published method reference:

| Call | How |
|---|---|
| `POST /byog/query`, `/byog/databases`, `/byog/collections` | raw HTTP, no SDK namespace exists |
| `client.query()` | `hydradb-sdk>=2` |
| `client.context.ingest()` / `status()` | `hydradb-sdk>=2` |
| `client.feedback.submit()` | `hydradb-sdk>=2` |

So the P0 hot path is one 15-line HTTP client, and the SDK appears only in the
P1 retrieval tier. Both use the same `HYDRADB_API_KEY` and the same base URL.

## 21. Risks and kill criteria

| Risk | Likelihood | Response |
|---|---|---|
| BYOG is not enabled on the hackathon key | unknown, highest | probe at hour 0. Fallback: `context/ingest` with `graph_payload`, which gives custom entities and relations but no Cypher traversal. Order then comes from metadata, and the pitch drops one notch. Say so. |
| `WHERE` on the entry node of a var-length pattern is rejected | low | fallback query in section 4 |
| Chains in the fixture are one hop deep | medium | kill criterion at hour 5. Design the fixture so families genuinely chain, and verify with a real second server too |
| "This is tool RAG" | certain to be said | ablation row: order validity collapses to ~0.6 without edges |
| Demo flakes on the network | medium | fixture server is local; cache the last successful graph read for the video, and say it is cached |
| Solo and behind at hour 12 | unknown | ship counters, order validity, one family. Drop P1 |
| Rate limit on `/byog/query` | unknown | reuse path means 0 calls on repeat tasks; measure QPS in the load run |

The one number that decides the project: does a four-tool chain exist where
each step needs the previous step's output. Test it before building anything.

## 22. Open questions

1. Does the hackathon API key have `/byog/*` enabled, and what is the query
   rate limit?
2. Where does the repo live? `life-os` is the career pipeline and this spec is
   here only because that is where the document was requested.
3. Is the opencode-as-client demo in scope? Point opencode at Switchboard and it
   sees 3 tools instead of 60. Strongest 20 seconds in the submission, about 30
   lines of config.
4. Hours left, and how many people.

---

## 23. Scale: the catalog is the index, not the payload

### The objection

HydraDB is built for large-scale context. Sixty tools is a toy. A judge will
say this, and they will be right if the graph only ever holds the catalog.

### The reframe

The catalog is static, small, and ingested once. What grows is everything the
tools touch and everything they return.

| Layer | Size | Grows |
|---|---|---|
| Tools, types, `FEEDS` edges | 60 to 10,000, one-time | no |
| Saved plans | one per distinct intent | yes, slowly |
| **Step results**, keyed by `(tool, args hash)` | one per distinct call | **yes, every run** |
| Decisions and step outcomes | one per call | **yes, every run** |

So the graph is not a tool directory. It is the agent's working set, and the
gateway adds to it every time it executes.

### The mechanism: step memoization

This is not entity resolution. There is no dedupe, no merge, no cross-source
identity, and no notion of "who this customer is." A result is keyed by the
exact `(tool_key, sha256(args))` that produced it, and nothing is ever combined
with anything else. Two tools that return the same real-world object stay two
nodes, because deciding they are the same is the agent's job and a second
product.

What it does: every tool declares what it `PRODUCES`. When a step runs, its
result is stored with `fetched_at` and an edge to the plan and step that
produced it. At plan time, each step is checked against the graph before it is
scheduled:

```
for step in plan:
    key = (step.tool, sha256(canonical(step.args)))
    if result(key) is fresh:  step.satisfied_from = "graph"   # no tool call
    else:                     step.satisfied_from = "tool"    # call, store result
```

TTL is per type, not global: reads 5 minutes, writes 0 (always call). A stale
node is not an error, it is just a miss.

This is common subexpression elimination for tool calls. It costs one node label
and one lookup, and it is only possible because the plan is data, not a prompt.

Metric: `steps_satisfied_from_graph / total_steps`. Reported per run, on
`/compare` and `/evals`.

Demo beat, 10 seconds: run task A (find issue 142, comment). Then run task B
("summarize issue 142"). Task B plans 1 step and makes 0 tool calls, because the
result of `get_issue(acme/api, 142)` is already stored. Wall time drops from
roughly 800 ms to 60 ms.

### The measurement: prove the query shape survives size

Seed a synthetic catalog at 100x today's size and rerun the planner benchmark.

| Shape | Value |
|---|---|
| Tools | 10,000 |
| `FEEDS` edges | 100,000, average out-degree 2 |
| Types | 200 |
| Query | the same one from section 4, anchored on 10 candidate keys and one goal |

Target: p95 planner latency within 2x of the 60-tool run. If it is not, the
query is wrong, not the database, and the fix is a tighter hop cap or a
`Tool.key` index, both already in the plan.

Guard against combinatorial blowup: the traversal is anchored at both ends
(10 known entries, 1 known goal), capped at 4 hops, `LIMIT 25`, 6 s ceiling.
If the call times out, fall back to a fetched adjacency and a local BFS. Log
which path was taken; a silent fallback is a lie.

Label the slide **synthetic scale test, not production traffic**. It answers
"does the query shape hold up," not "we have users."

### The question every graph project gets: why not Neo4j

Because HydraDB does two things in one place that a pure graph database does
not:

1. **Structure.** BYOG traversal for order and satisfaction. Neo4j does this
   equally well, and pretending otherwise is dishonest.
2. **Semantics.** `POST /query` over ingested sources, hybrid BM25 plus
   semantic, with graph context in the same response. This is the part Neo4j
   does not have.

So the design must actually use (2), or the honest answer to "why HydraDB" is
"because the hackathon required it." The cheapest real use: when BM25 over the
tool descriptions scores below a threshold, ask `client.query()` over ingested
tool docs and past failure notes to pick the goal tool. Two tiers, one counter,
and the retrieval path is only paid when the deterministic path is unsure.

P1, after the demo is recorded, because `context/ingest` is asynchronous and a
1 to 5 minute indexing wait has no place in a live demo.

### What not to add

| Idea | Why not |
|---|---|
| Entity resolution, dedupe, merge | a second product, and the agent's job |
| Semantic memory of preferences | Lacuna and HydraMind already won this track with it |
| Vector search of tool descriptions | `client.query()` already is that, with less code |
| Graph visualisation | demos as a picture, not a number |
| More MCP servers | the fixture is the test harness; three real servers is the ceiling |
| Cross-tenant federation | real GTM story, but it is auth and isolation, both out of scope |
| Plan repair under catalog drift | strong, graph-native, and a real production failure. Only if P0 is clean by hour 18 |
