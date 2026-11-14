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

## 2. Not proven

| # | Check | Blocked by |
|---|---|---|
| N1 | 60-tool fixture has six real 4-tool chains | fixture not built |
| N2 | `switchboard index` seeds 60 tools and derived edges | CLI/index not built |
| N3 | `find` / `run` MCP round-trip in a real client | gateway not built |
| N4 | `switchboard add mcp` / `list` / `remove` / `serve` | CLI not built |
| N5 | Removal test: empty graph means `NoPlan` | planner not built |
| N6 | Baseline 60 vs gateway 2 in OpenCode | A/B not built |
| N7 | 5-round learning curve on a holdout | evals not built |
| N8 | HTTP daemon with two clients | HTTP not built |
| N9 | Landing page, public repo, Loom | submission work |

## 3. Stack runbook (verified)

MinIO on 19000, HydraDB on 18443. The host already runs another MinIO on 9000,
so do not use that port. The gateway runs from the host, not from Compose.

```bash
cp .env.example .env
docker compose up -d --wait

docker compose ps
curl -fsS http://127.0.0.1:19090/readyz

python scripts/probe.py
# PASS readyz: 200
# PASS node batch: 4 tools
# PASS edge batch: 3 edges, rerun stable
# PASS path query: probe_a -> probe_b -> probe_c -> probe_d
# PASS plan steps: probe_a -> probe_b -> probe_c -> probe_d
# PASS edge weight: weight 0.25 hydrated
# 6/6 PASS
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

## 5. Gateway checks (target)

These are the checks to run once the CLI and gateway land. They are not proven
yet.

### CLI and config

```bash
switchboard add mcp issues -- python -m fixtures.issue_server
switchboard list
switchboard remove mcp issues
switchboard add mcp issues -- python -m fixtures.issue_server
```

Pass: the config file contains only the command, `list` prints the server and
tool count, and no secret is written.

### Index

```bash
switchboard index
switchboard index
```

Pass: 60 tools, the same node and edge counts on both runs, and no duplicate
edges. Query back one cross-server `FEEDS` edge and one 4-tool path.

### Plan

```bash
switchboard plan "comment on the failing test issue in acme/api"
```

Pass: four tool keys in order, every step's consumed types produced earlier, a
plan id printed, and `NoPlan` when the graph is empty.

### MCP round-trip

Start the gateway:

```bash
switchboard serve
```

It speaks MCP over stdio, so a human shell is not the client. Use the OpenCode
config below or an MCP inspector.

Pass: `tools/list` returns exactly `find` and `run`; `find` returns a plan;
`run` executes four upstream calls and returns four results.

### OpenCode baseline vs gateway

Baseline config, in `evals/harness/baseline/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "issues": {
        "type": "local",
        "command": ["python", "-m", "fixtures.issue_server"],
        "codemode": false
      },
      "repo": {
        "type": "local",
        "command": ["python", "-m", "fixtures.repo_server"],
        "codemode": false
      },
      "incident": {
        "type": "local",
        "command": ["python", "-m", "fixtures.incident_server"],
        "codemode": false
      },
      "account": {
        "type": "local",
        "command": ["python", "-m", "fixtures.account_server"],
        "codemode": false
      }
    }
  }
}
```

Gateway config, in `evals/harness/gateway/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "switchboard": {
        "type": "local",
        "command": ["switchboard", "serve"],
        "codemode": false
      }
    }
  }
}
```

Run each from its own directory:

```bash
cd evals/harness/baseline
opencode mcp list
opencode run --format json "comment on the failing test issue in acme/api"
```

Pass: baseline exposes 60 native tools; gateway exposes 2; the gateway plan has
4 steps and `run` executes them. Record tool calls, tokens, and errors.

`codemode: false` matters. With OpenCode's default Code Mode, MCP tools are
grouped behind one executor, so the baseline's 60 tools are not a native tool
list and the counter is not comparable. Turn it off on both sides.

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
  both sides of the A/B.
- The engine reports anonymous usage by default. Disable it for the demo.
- The HydraDB image runs as uid 10001. Named volumes start root-owned; the init
  service must chown them or the container exits 1.
