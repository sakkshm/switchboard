# Backend verdict and test plan

Status: 2026-11-15. The hackathon requires the open-source engine, so the
managed platform is out. This file records what was proven, what is still open,
and the order to build. Submission cutoff: 9 AM IST, Nov 16.

## 0. Verdict

Use HydraDB OSS 0.4.0 with MinIO (S3-compatible storage). Do not use
`CLOUD_PROVIDER=local` / `LOCAL_PATH` for anything that writes.

The local filesystem store does not implement `PutMode::Update`. That broke
plan counters, type/agent/decision nodes, list properties, and some batch
writes. MinIO fixed every one of them. Pin the image digest for the demo.

| Check | LOCAL_PATH | MinIO |
|---|---|---|
| Engine starts | PASS | PASS |
| Node batch upsert | partial | PASS |
| Edge batch upsert, rerun stable | FAIL | PASS |
| Plan steps, `ORDER BY s.i` | PASS | PASS |
| `wins` update | FAIL | PASS |
| Type, grant, decision nodes | FAIL | PASS |
| List property | FAIL | PASS |
| Edge weight in path result | not tested | PASS |
| Restart persistence | PASS | PASS |

## 1. Proven on OSS + MinIO

| # | Check | Evidence |
|---|---|---|
| B1 | Six-node, six-edge seed | node batch then edge batch, counts match |
| B2 | Edge batch is idempotent | rerun: 6 edges before and after |
| B3 | Four-tool ordered path | `algo.MSpaths` returned `repo_search -> list_issues -> get_issue -> add_comment` |
| B4 | Multiple paths per source | direct 1->4 and 4-node chain both returned |
| B5 | `maxLen` counts hops | maxLen 1 returns the direct edge; maxLen 2 excludes the 3-hop chain; maxLen 3 includes it |
| B6 | Plan steps and order | `MERGE (p)-[:STEP {i}]->(t)`, `ORDER BY s.i` returns 1,2,3,4 |
| B7 | Plan counter | create with `wins` in the row, update with `SET p.wins = $w` |
| B8 | Type edges | `(:Tool)-[:CONSUMES]->(:Type)` created and read |
| B9 | Grants | `(:Agent)-[:GRANTED]->(:Tool)` created and read |
| B10 | Decisions | `(:Decision {ts, verdict, reason})` created and read |
| B11 | List property | `SET t.consumes = ['repo','issue']` reads back as a list |
| B12 | Grouped count | `RETURN t.key AS k, count(*) AS n` works |
| B13 | Edge weights | `SET r.weight`, batch update, hydrated in the path result |
| B14 | Restart | node, edge, step, wins, and weight all survived |
| B15 | Compose stack | MinIO and HydraDB healthy, probe 6/6 on 2026-11-14 |
| B16 | Named-volume ownership | image runs as uid 10001; init service chowns the cache volume |

The product uses B1, B2, B3, B6, B11, B13, and B16. B8, B9, and B10 were
verified but are not in the new P0 graph. They can return later for grants and
audit.

## 2. Product gates (2026-11-15)

| # | Check | Result | Evidence |
|---|---|---|---|
| N1 | 60-tool fixture has six real 4-tool chains | PASS | `evals/chain_check.py` |
| N2 | `index` seeds 60 tools and derived edges, twice | PASS | 60 tools, 69 edges both runs |
| N3 | `find` / `run` round-trip in OpenCode v2.2.5 | PASS | 4-step plan `227123842785482`, `run` ok |
| N4 | `add mcp` / `list` / `remove` / `serve` | PASS | echo server added, listed, removed; stdio and HTTP serve |
| N5 | Removal test: empty graph means `NoPlan` | PASS | `tests/unit/test_bindings.py` |
| N6 | Baseline 60 vs gateway 2 in OpenCode | PARTIAL | gateway path PASS; 60-tool direct baseline blocked by client, see section 5.4 |
| N7 | 5-round learning curve on a holdout | PASS | `evals/results/latest.json` |
| N8 | HTTP daemon with two concurrent clients | PASS | both saw 2 tools, both ran a 4-step plan |
| N9 | Landing page, public repo, Loom | landing built; repo public; Loom recorded by the user |

The learning curve, token measurements, trap-route effect, and the OpenCode
caveat are written up in `docs/RESULTS.md`.

## 3. Stack runbook (verified)

MinIO on 19000, HydraDB on 18443. The host already runs another MinIO on 9000,
so do not use that port. The gateway runs from the host, not from Compose.

```bash
cp .env.example .env
docker compose up -d --wait

docker compose ps
curl -fsS http://127.0.0.1:19090/readyz

switchboard status
# HydraDB http://127.0.0.1:18443  ready=True
# servers=N tools=60 edges=E plans=P
```

Logs:

```bash
docker compose logs -f hydradb
docker compose logs -f minio
```

Reset to a clean store:

```bash
docker compose down -v
docker compose up -d --wait
```

The Compose file has a busybox `hydradb-auth` init service. It writes the token
to a named volume and chowns that volume and `hydradb-cache` to 10001. Without
the chown, HydraDB exits with `Permission denied` on `/data/cache/shared`.

Endpoint: `POST http://127.0.0.1:18443/v1/graphs/default/query`
Headers: `Authorization: Bearer local-development-token-32-bytes`,
`X-Graph-Namespace: default`, `Content-Type: application/json`.
Body: `{"cell_id":"cell-0","query":"...","parameters":{...}}`.
Rows are typed cells: `[[{"type":"string","value":"repo_search"}]]`.
A path cell has ordered `nodes` and `relationships`, each with typed
`properties`. Decode before scoring.

## 4. Cypher cookbook (verified)

```cypher
-- node batch, idempotent, labels and properties after the MERGE
UNWIND $rows AS row
MERGE (n {id: row.id})
SET n:Tool, n.key = row.key, n.name = row.name

-- edge batch, idempotent, no labels in the pattern, chunk at 1000
UNWIND $rows AS row
CREATE (a {id: row.src})-[:FEEDS]->(b {id: row.dst})

-- planner: one call, all candidate sources, ordered paths
CALL algo.MSpaths({
  sourceLabel: 'Tool', sourceProperty: 'key', sourceValues: ['a','b'],
  targetLabel: 'Tool', targetProperty: 'key', targetValues: ['goal'],
  relTypes: ['FEEDS'], relDirection: 'outgoing',
  maxLen: 6, pathCount: 10, pairwise: true, resultLimit: 50
}) YIELD path RETURN path

-- plan step, idempotent
MERGE (p:Plan {id: 100})-[:STEP {i: 0}]->(t:Tool {id: 1})

-- read a plan in order
MATCH (p:Plan {id: 100})-[s:STEP]->(t:Tool)
RETURN t.key AS k ORDER BY s.i

-- counter, read then write a literal or parameter
MATCH (p:Plan {id: 100}) SET p.wins = $w

-- learning signal, one batch per round
UNWIND $rows AS row
MATCH (a {id: row.src})-[r:FEEDS]->(b {id: row.dst})
SET r.weight = row.w
```

Do not use: `MERGE` with a label followed by `SET` (use label-free `MERGE`
then `SET`), property arithmetic such as `p.wins + 1`, `RETURN p` or list
comprehensions, `LOCAL_PATH` for writes, or Cypher index creation in 0.4.0.

`MSpaths` omits pairs with no path. Join results back by endpoint id.
`sourceValues` must be a literal list of validated keys, not a parameter.

## 5. Gateway checks (verified 2026-11-15)

Run from the repo root with `.venv` active and
`SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json` unless stated otherwise.

### CLI and config

```bash
SWITCHBOARD_CONFIG=/tmp/opencode/cli-test.json \
  switchboard add mcp echo -- .venv/bin/python -m tests.fixtures.echo_server
SWITCHBOARD_CONFIG=/tmp/opencode/cli-test.json switchboard list
SWITCHBOARD_CONFIG=/tmp/opencode/cli-test.json switchboard remove mcp echo
```

Observed: `added echo: 2 tools`, then the server with its command, then
`removed echo`. The config file contains only the command, and no secret is
written. Tool counts in `list` come from the graph, so a new server shows
`0 tools` until the next `index`.

### Index

```bash
switchboard index
switchboard index
switchboard status
```

Observed on both runs: `indexed 60 tools, 69 FEEDS edges`, then
`servers=4 tools=60 edges=69`. Re-running does not duplicate nodes or edges,
and does not reset learned weights. The `plans=N` field grows with `find` calls;
index does not create plans.

### Plan

```bash
switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced
```

Observed: a plan id, `repo.repo_search -> repo.list_issues -> issue.get_issue ->
issue.add_comment`, and an input list of `repo`, `message`. With an empty graph,
`find` raises `NoPlan` (unit test `test_empty_graph_has_no_plan`).

### MCP round-trip

```bash
switchboard serve                    # stdio
switchboard serve --http 127.0.0.1:8765   # HTTP daemon
```

`tools/list` returns exactly `find` and `run` on both transports. The HTTP
daemon was tested with two concurrent MCP clients, each on its own session:

```
A tools=['find', 'run'] plan=266372913974742 steps=4
B tools=['find', 'run'] plan=235552781383402 steps=4
A run ok=True steps=4 final="comment on acme/api#42: failing test in checkout: reproduced"
B run ok=True steps=4 final="comment on acme/api#42: failing test in checkout: reproduced"
```

### OpenCode baseline vs gateway

OpenCode reads project config from the git root, not from a subdirectory. The
two ready configs are drop-ins:

- `evals/harness/baseline/opencode.json` (four fixture servers, `codemode: false`)
- `evals/harness/gateway/opencode.json` (one gateway server, `codemode: false`)

Copy one to the repo root as `opencode.json`, then run from the repo root:

```bash
OPENCODE_DISABLE_AUTOUPDATE=1 opencode --standalone run --auto --format json \
  -m opencode/deepseek-v4.1-flash-free \
  "Task: comment on the failing test issue in acme/api. Inputs: repo=acme/api, message=reproduced. Use the switchboard find tool, then switchboard run with the plan_id and the same inputs."
```

Gateway, observed: `mcp connected server=switchboard tools=2`; the model called
`switchboard_find` (plan `227123842785482`, four steps) and `switchboard_run`
(`ok: true`, four results, final result
`comment on acme/api#42: failing test in checkout: reproduced`).

Baseline, observed: the four servers connect (`tools=15` each, 60 total), but
OpenCode v2.2.5 does not put them in the model's tool catalog. A control server
with two trivial tools stayed invisible too. This is a client limitation, not a
gateway failure; the exact 60-vs-2 token cost is measured from the MCP
`tools/list` payloads in `evals/baseline_tokens.py`. Do not claim a live
60-native-tool model run. Full log of attempts: `docs/RESULTS.md`.

`opencode mcp list` reports "No MCP servers configured" even when the debug log
says `mcp connected`. Use `--print-logs --log-level debug` and look for
`mcp connected`, or inspect the tool calls in `--format json` output.

## 6. Track and judging

Pick **Track 2, the agent context layer**. The product is the described shape:
the agent asks for a task, the gateway traverses tool capabilities, and only the
four needed tools are named in the plan.

The judging criteria also demand a learning curve and a baseline:

- Baseline: BM25 rank order, no graph.
- Static graph: `MSpaths` with uniform edge weights.
- Learning graph: after each task, `FEEDS.weight` goes up on success and down
  on failure; the planner scores paths by candidate coverage plus weight.
- Run 5 rounds on a train split, report top-1 plan accuracy and task success on
  a held-out split.
- The fixture must contain distractor paths, or weights have nothing to learn.

Right metrics: top-1 plan accuracy, task success, order validity, tools and
tokens in context, graph calls per task, p95 plan latency. Not a single timing.

### What not to claim

- Do not claim four tools in context. The client sees two gateway tools; the
  plan names four upstream tools. Say both numbers.
- Do not claim a hosted live link. The demo is a local gateway and a Loom.
- Do not claim the managed semantic retrieval tier. OSS has traversal, plans,
  and weights.
- Do not claim the 60 tools are real. They are our fixture servers.

## 7. Build order to the deadline

| Order | Work | Gate |
|---|---|---|
| 1 | CLI, config, gateway skeleton, one fixture server | G1 |
| 2 | Four fixture servers, 60 tools, 6 chains, chain_check | G2 |
| 3 | Index, matcher, planner, validator, removal test | G3 |
| 4 | Binder, executor, `find` / `run`, OpenCode A/B | G4 |
| 5 | Eval harness, BM25/uniform/learned, 5-round curve | G5 |
| 6 | HTTP daemon, landing page, public repo, Loom | G6 |

Cut if behind: HTTP, write refusal, plan reuse, adversarial set, scale test,
LLM baseline. Never cut: removal test, eval numbers, BM25 baseline, honest
2-tools-vs-4-steps line.

## 8. Traps

- `mcp` 2.x renamed `FastMCP` to `MCPServer`; `CallToolResult.isError` is
  `is_error`. Pin the version or use the new API.
- A stdio MCP server must not print logs to stdout. Use stderr for every log,
  including tracebacks from upstream tools.
- OSS uses integer `id` for MERGE, not an arbitrary key. Map `server.tool` to a
  stable id at index time.
- Edge batches use `CREATE` without labels and dedupe on `(src, dst, type)`.
  Chunk at 1000; the cap is 1024.
- `MSpaths` returns all paths for a source-target pair, not a ranked plan.
  Scoring stays in Python.
- Edge weight expressions are not supported. Read, add in Python, write a
  literal or parameter.
- Config headers must store `{env:NAME}`, not the secret. The CLI must not echo
  the resolved value.
- The fixture capability header is a convention, not MCP. Real servers need
  output types or a manifest. Say so.
- OpenCode with default Code Mode groups MCP tools; set `codemode: false` for
  both sides of the A/B. In v2.2.5 this surfaced the gateway's 2 tools but not
  the fixture servers' tools, so the live 60-tool baseline is blocked.
- HydraDB HTTP writes dedupe on `query_id`; the server's auto counter resets on
  restart and can replay an old mutation. Send a unique id per write.
- Round edge-weight updates before comparing paths. Three updates of 1.4 can
  render as `1.3999999999999997` and lose to an equal-weight longer path.
- The engine reports anonymous usage by default. Disable it for the demo.
- The HydraDB image runs as uid 10001. Named volumes start root-owned; the init
  service must chown them or the container exits 1.
