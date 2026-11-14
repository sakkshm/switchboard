# Backend verdict and test plan

Status: 2026-11-14. The hackathon requires the open-source engine, so the
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
| Edge batch upsert, rerun stays 6 | FAIL | PASS |
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
| B15 | Footprint | HydraDB 17 MB, MinIO 152 MB |

## 2. Not proven

| # | Check | Blocked by |
|---|---|---|
| N1 | 60-tool fixture has six real 4-tool chains | fixture not built |
| N2 | Seed at 60 tools / 100 edges | fixture not built |
| N3 | 24-task golden set, baseline vs learning curve | evals not built |
| N4 | `find` / `describe` / `run` MCP round-trip | gateway not built |
| N5 | Removal test: empty graph means `NoPlan` | planner not built |
| N6 | Read-only refusal, second-run counters | executor/UI not built |
| N7 | Live link, public repo, Loom | submission work |
| N8 | 20 concurrent `/compare` | app not built |

## 3. Stack runbook (verified)

MinIO on 19000, HydraDB on 18443. The host already runs another MinIO on 9000,
so do not use that port.

```bash
mkdir -p /tmp/hydra/minio /tmp/hydra/cache
printf '%s\n' 'local-development-token-32-bytes' > /tmp/hydra/auth-token
chmod -R 777 /tmp/hydra

docker network create hydradb-net

docker run -d --name minio --network hydradb-net \
  -p 19000:9000 -p 19001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \
  -v /tmp/hydra/minio:/data \
  minio/minio server /data --console-address ":9001"

docker run --rm --network hydradb-net --entrypoint /bin/sh minio/mc -c \
  "mc alias set local http://minio:9000 minioadmin minioadmin &&
   mc mb -p local/hydradb"

docker run -d --name hydradb --network hydradb-net \
  -p 18443:8443 -p 17687:7687 -p 19090:9090 \
  -e CLOUD_PROVIDER=aws -e AWS_BUCKET_NAME=hydradb \
  -e AWS_DEFAULT_REGION=us-east-1 -e AWS_ALLOW_HTTP=true \
  -e AWS_ENDPOINT=http://minio:9000 \
  -e AWS_ACCESS_KEY_ID=minioadmin -e AWS_SECRET_ACCESS_KEY=minioadmin \
  -e AWS_VIRTUAL_HOSTED_STYLE_REQUEST=false \
  -e GRAPH_DATA_PATH=slatedb-graph/data -e GRAPH_DATABASE=default \
  -e GRAPH_NAMESPACE=default -e GRAPH_ID=default \
  -e GRAPH_CELL_ID=cell-0 -e GRAPH_CELLS=cell-0 -e GRAPH_NODE_ID=node-0 \
  -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
  -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
  -e GRAPH_DATA_CACHE_DIR=/data/cache \
  -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token \
  -e GRAPH_ALLOW_PLAINTEXT=true -e RUST_MIN_STACK=33554432 \
  -e GRAPH_USAGE_REPORT_ENABLED=false \
  -v /tmp/hydra/cache:/data/cache -v /tmp/hydra/auth-token:/data/auth-token \
  ghcr.io/hydra-db/hydradb:latest
```

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

## 5. Track and judging

Pick **Track 2, the agent context layer**. The product is the described shape:
the agent asks for a task, the gateway traverses tool capabilities, and only the
four needed tools enter the window.

The technical criteria also demand a learning curve and a baseline, so add one
small loop that is already supported:

- Baseline: BM25 rank order, no graph.
- Static graph: `MSpaths` with uniform edge weights.
- Learning graph: after each task, `FEEDS.weight` goes up on success and down
  on failure; the planner scores paths by candidate coverage plus weight.
- Run 5 rounds on a train split, report top-1 plan accuracy and task success on
  a held-out split. Expect the learning line to rise over static and baseline.
- The fixture must contain distractor paths, or weights have nothing to learn.

Right metrics: top-1 plan accuracy, task success, order validity, tools and
tokens in context, graph calls per task, p95 plan latency. Not a single timing.

### P1: LLM graph bootstrap

Offline only, after the core demo works. One call per tool family proposes
`consumes` / `produces` from names, descriptions, and schemas; the result is
cached in `catalog/ontology.json`, diffed, and committed. The demo seeds from
the cache, so the Loom never depends on a model being up.

This improves onboarding for real MCP servers, which do not declare outputs. It
does not replace the learning loop: a one-time inference is a baseline, not a
curve. Measured against the fixture headers it gives a precision/recall number,
and the validator plus execution reject bad edges.

Do not claim the managed `client.query()` semantic tier. The OSS engine does
not have it. The graph-native claim is traversal plus saved plans plus weights.

## 6. Build order to the deadline

| Order | Work | Gate |
|---|---|---|
| 1 | Fixture catalog, 60 tools, 6 chains, chain_check | G1 |
| 2 | Store client, typed-cell decoder, seed, reset | G2 |
| 3 | Matcher, planner, validator, removal test | G3 |
| 4 | Binder, executor, grants, audit | G3 |
| 5 | `/compare`, counters, chain strip, read-only toggle | G4 |
| 6 | Eval harness, baseline, 5-round learning curve | judging |
| 7 | Host app, public repo, Loom, social post | submission |

Cut if behind: scale test, trace page, memoization, LLM baseline, catalog page,
eval HTML. Never cut: removal test, eval numbers, blocked write, honest backend
line, live link.

## 7. Traps

- `mcp` 2.x renamed `FastMCP` to `MCPServer`; `CallToolResult.isError` is
  `is_error`. Pin the version or use the new API.
- OSS uses integer `id` for MERGE, not an arbitrary key. Map catalog keys to
  stable ids at seed time.
- Edge batches use `CREATE` without labels and dedupe on `(src, dst, type)`.
  Chunk at 1000; the cap is 1024.
- `MSpaths` returns all paths for a source-target pair, not a ranked plan.
  Scoring stays in Python.
- Edge weight expressions are not supported. Read, add in Python, write a
  literal or parameter.
- The engine reports anonymous usage by default. Disable it for the demo.
