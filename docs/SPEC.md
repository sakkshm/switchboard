# Switchboard

One MCP gateway for every tool. The agent asks for a task and gets a four-step
plan through a graph, not a sixty-tool catalog.

Status: product spec for the Nov 16 submission. The HydraDB OSS backend is
verified. The CLI, gateway, fixtures, and evals are in build.

---

## 0. Status and the two corrections

The backend decision is settled: HydraDB OSS 0.4.0 with MinIO, run by Docker
Compose. The managed platform and `BYOG` are out of scope. `TESTING.md` records
the verified query shapes and traps.

The integration decision is settled: a universal MCP gateway, not an OpenCode
plugin and not a web app.

An earlier draft filtered the tool list inside an OpenCode plugin. That gives a
literal four tools in one client, but it is OpenCode-only and the product stops
being an MCP gateway. A second draft exposed `find`, `describe`, and `run`,
which is three tools and a fallback path that weakens the removal test. Both are
out.

The gateway is one MCP server that fronts N upstream MCP servers. Any MCP client
can use it. The client sees two tools, `find` and `run`.

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

The fix is not a better description. It is to stop putting the catalog in the
window.

## 2. Product

Switchboard is a local MCP gateway with a CLI.

- `switchboard add mcp <name>` registers an upstream server once. The same
  gateway serves every MCP client on the machine.
- It discovers tools with `tools/list` and holds the catalog, so the agent never
  does.
- It returns a plan: the tools that chain, in order, plus the argument bindings.
- It executes the plan itself, binding arguments between steps.
- It learns which edges lead to successful runs and uses those weights when
  choosing among paths.

One line: the agent never loads 60 tools. It asks for a task and gets a path
through the tool graph, plus a proxy that runs it.

Non-goals: entity resolution, dedupe, cross-source identity, memory products,
agent frameworks, vector databases, auth, deployment.

## 3. Why a gateway, not a plugin

A plugin can see the client's entire tool list and delete tools per request.
That is the only way to show a literal four tools. It is also OpenCode-only.

A gateway is portable. OpenCode, Claude Desktop, Cursor, and a custom agent all
speak MCP. The same graph, plans, and learned weights serve all of them.

The gateway cannot see tools the client has outside the gateway. That is the
correct boundary: you add the servers you want it to manage. `switchboard add
mcp` is the whole configuration.

The tradeoff is honest: the client sees two gateway tools, not four upstream
tools. The four is the plan length. A gateway cannot advertise a task-specific
tool list before it has the task.

## 4. Architecture

```
Any MCP client (OpenCode, Claude Desktop, Cursor, custom agent)
    |  stdio: switchboard serve
    |  or HTTP: http://127.0.0.1:8765/mcp
    v
Switchboard MCP server
    |  tools/list -> find, run
    |-- matcher    BM25 over discovered tools -> goal + candidates
    |-- planner    one /v1/graphs/default/query -> ordered path
    |-- validator  consumes/produces check on the returned path
    |-- binder     $input / $steps.N.result -> tool args
    |-- executor   upstream tools/call, timeouts, output caps
    +-- learner    FEEDS.weight updates from run outcomes
    |
    v
HydraDB OSS 0.4.0  (Docker Compose)
    |  POST /v1/graphs/default/query
    v
MinIO (S3-compatible)

N upstream MCP servers (fixtures, filesystem, git, remote HTTP)
```

No LLM on the default path. No vector store. No web app.

## 5. CLI and config

```bash
switchboard add mcp issues -- python -m tests.fixtures.issue_server
switchboard add mcp github --url https://api.githubcopilot.com/mcp \
  --header "Authorization=Bearer {env:GITHUB_TOKEN}"
switchboard remove mcp issues
switchboard list
switchboard index
switchboard tools
switchboard graph
switchboard annotate github get_file_contents --consumes owner,repo,path --produces content
switchboard reset --yes
switchboard plan "comment on the failing test issue in acme/api"
switchboard run <plan_id> --input repo=acme/api --input message="on it"
switchboard serve
switchboard serve --http 127.0.0.1:8765
switchboard status
```

Config lives in `~/.config/switchboard/config.json`. It is upstream connection
data, not a catalog:

```json
{
  "servers": {
    "issues": {
      "transport": "stdio",
      "command": ["python", "-m", "tests.fixtures.issue_server"]
    },
    "github": {
      "transport": "http",
      "url": "https://api.githubcopilot.com/mcp",
      "headers": { "Authorization": "Bearer {env:GITHUB_TOKEN}" }
    }
  },
  "hydradb": {
    "url": "http://127.0.0.1:18443",
    "graph": "default",
    "token_env": "HYDRADB_TOKEN"
  }
}
```

Rules:

- `{env:NAME}` is resolved at runtime. The CLI never writes the value.
- `switchboard add` validates the command or URL and, if possible, performs a
  `tools/list` probe. It fails loudly rather than storing a broken server.
- Tools are discovered at `index` and at `serve` startup. No tool names or
  descriptions are stored in the config.
- `switchboard list` prints servers, transport, connection state, and tool count.
- `switchboard status` prints HydraDB health, tool/edge/plan counts, and the
  current weights.
- `switchboard annotate` writes consumes/produces for one upstream tool into the
  config. MCP publishes no dependency types, so this is how a real server gets
  edges. `switchboard tools` and `switchboard graph` print what was indexed.

## 6. MCP surface

Exactly two MCP tools. The agent never sees the real catalog.

| Tool | Input | Returns |
|---|---|---|
| `find` | `task`, optional `inputs` | `plan_id`, four ordered steps, missing inputs |
| `run` | `plan_id`, optional `inputs` | one result per step, or the failing step |

`find` does not return tool schemas. The agent does not need them because `run`
executes on the gateway side. That is the context saving and the boundary.

There is no `describe` and no fallback catalog. If the graph has no path, `find`
returns `NoPlan`. That is deliberate: a fallback would hide a broken graph.

The transport is stdio by default and Streamable HTTP for a shared daemon.
`MCPServer.run("stdio")` and `run("streamable-http")` in `mcp` 2.3.0 support
both. Logs go to stderr; stdout is protocol.

## 7. Graph schema

```
(:Tool {id, key, name, server, consumes, produces, writes})
(:Tool)-[:FEEDS {weight}]->(:Tool)
(:Plan {id, key, task, hash, wins, losses, ts})
(:Plan)-[:STEP {i, args}]->(:Tool)
```

- `id` is a stable integer derived from `server.tool`, used for `MERGE`.
- `key` is the human-readable `server.tool`, used in plans and paths.
- `consumes` and `produces` are lists of type names parsed from the tool
  description header.
- `FEEDS` is derived at index time, cross-server, because the point is chaining
  across servers.
- `STEP` is a star from the plan to each tool, with the index and the argument
  template on the relationship. Reading a plan back is
  `MATCH (:Plan {key:$k})-[s:STEP]->(t:Tool) RETURN t.key ORDER BY s.i`.
- `weight` starts at 1.0. The learner updates it after a run.

No `Type` nodes in P0. Lists on `Tool` are enough and there are fewer writes.
No `Agent`, `GRANTED`, or `Decision` nodes in P0. Auth is out of scope.

## 8. Planner

1. Task text in.
2. BM25 over tool descriptions. Take a goal tool and up to 10 candidates.
3. One graph call: `algo.MSpaths` from the candidates to the goal, six hops max.
4. Score each returned path by candidate coverage, minus a hop penalty, plus
   the learned weights. Pick the best.
5. Validate types: for each step, every consumed type must already be produced
   by an earlier step or supplied by the input. Reject the plan if not.
6. Write the plan to HydraDB. Return it to the client.

If there is no path, raise `NoPlan`. No local topological sort, no fallback
order, no model call.

The query:

```cypher
CALL algo.MSpaths({
  sourceLabel: 'Tool', sourceProperty: 'key', sourceValues: [...],
  targetLabel: 'Tool', targetProperty: 'key', targetValues: [goal],
  relTypes: ['FEEDS'], relDirection: 'outgoing',
  maxLen: 6, pathCount: 10, pairwise: true, resultLimit: 50
}) YIELD path RETURN path
```

`sourceValues` must be a literal list of validated keys, not a parameter. The
path order is the plan order. Python scores, it does not sort tools by hand.

## 9. Plan format and binding

```json
{
  "plan_id": "p_01J...",
  "task": "comment on the failing test issue in acme/api",
  "steps": [
    {"i": 0, "tool": "repo_search", "args": {"query": "$input.repo"}},
    {"i": 1, "tool": "list_issues", "args": {"repo": "$steps.0.result.full_name", "state": "open"}},
    {"i": 2, "tool": "get_issue", "args": {"repo": "$steps.0.result.full_name", "number": "$steps.1.result.0.number"}},
    {"i": 3, "tool": "add_comment", "args": {"repo": "$steps.0.result.full_name", "number": "$steps.2.result.number", "body": "$input.message"}}
  ],
  "inputs": ["repo", "message"],
  "missing_inputs": ["repo", "message"]
}
```

Three binding sources only: `$input.*`, `$steps.N.result.*`, and constants.
Anything else means the plan is wrong and must fail loudly, not improvise.

This is why the gateway executes. If the agent executes, the binding lives in
the model's head and order can drift. If the plan executes, the binding is data
and order cannot drift.

## 10. Execution and safety

- `run` reads the plan from HydraDB by id, validates it again, and executes
  sequentially.
- Arguments are validated against the upstream tool's JSON Schema. Unknown
  fields are rejected.
- Each upstream call has a 10 s timeout and an output size cap. Control
  characters are stripped.
- A tool marked destructive is refused unless the server was added with
  `--allow-writes` or the request passes `allow_writes`. P1, cheap.
- One JSON log line per step: plan id, step, tool, ms, status, error.
- No shell tool, no arbitrary SQL, no remote code.

There is no auth in the demo. It binds to 127.0.0.1. The README says so.

## 11. Learning loop

A successful step raises the weight on the `FEEDS` edge that led to it. A failed
step lowers it. The planner reads the weight in the path result and includes it
in the path score.

The experiment has three modes, all on the same fixture and tasks:

| Mode | Order source | Weights |
|---|---|---|
| `bm25` | BM25 rank, top 4 | none |
| `uniform` | graph path | all 1.0 |
| `learned` | graph path | updated by outcomes |

Train for 5 rounds on 18 tasks, measure on 6 held-out tasks after each round.
Report top-1 plan accuracy, order validity, and task success. The learned line
must rise above uniform and BM25 on the holdout, or the fixture has no ambiguity
and the claim is dropped.

The fixture needs distractor paths: more than one plausible route to the same
goal, where the wrong route fails at execution. Without that, there is nothing
to learn and the curve is fake.

## 12. Evals

Golden set: 24 tasks, 4 families, 6 each. Fields: task, expected ordered tools,
expected input values, allowed extras, expected end state.

| Metric | Target |
|---|---|
| Tool precision | >= 0.90 |
| Tool recall | 1.00 on P0 families |
| Order validity | 1.00 for the graph modes |
| BM25 baseline order validity | measure, expect 0.55 to 0.70 |
| Task success | >= 0.85 |
| Tools in context | 60 baseline, 2 gateway |
| Graph calls per task | 1, 0 on a reused plan |
| p95 plan latency | < 400 ms |

Order validity is structural: every step consumes a type produced by an earlier
step, or supplied by the input. No LLM judge, no rubric.

The exact tool-schema token count is computed from each `tools/list` response,
not estimated. The eval reports it for baseline and gateway.

The full 24-task eval drives the gateway directly for determinism. OpenCode runs
one task per family as the end-to-end check. Both are reported; neither is
presented as the other.

## 13. Demo

Three minutes, counters first. The client is OpenCode.

| Time | Beat |
|---|---|
| 0:00 | Baseline session: four fixture servers, 60 tools in context |
| 0:20 | Run the task. The model picks tools and retries on order |
| 0:50 | Gateway session: one server, two tools |
| 1:10 | `find` returns the four-step plan; show the graph path |
| 1:30 | `run` executes; show the four results and the side effect |
| 2:00 | Show the gateway log: 60 discovered, 4 planned, weights updated |
| 2:20 | Eval table: BM25 vs uniform vs learned |

60 second cut: counters, plan, run, eval table.

No web UI. The landing page is static and links the Loom.

## 14. What is real and what is mocked

| Piece | Status |
|---|---|
| HydraDB graph, traversal, plans, weights | real, OSS 0.4.0 |
| CLI, gateway, planner, binder, executor | real |
| 60-tool catalog | synthetic, four fixture MCP servers |
| Capability headers | our convention; real servers need output types |
| Baseline and learning numbers | measured on the fixture |
| OpenCode sessions | real MCP clients |
| Auth, multi-tenancy | not built |
| Landing page | static |

The 60 tools are synthetic on purpose. Real MCP servers rarely declare what they
produce, so they cannot prove a four-type chain. The fixture is the test
harness, and the README says so.

## 15. Why HydraDB

A JSON file plus a local BFS can order 60 tools. That is the honest baseline.

HydraDB earns its place because the graph is also the memory:

- Plans are graph objects, so a second similar task is a traversal, not a
  recomputation.
- Edge weights persist across sessions and clients, so the gateway learns from
  every agent that uses it.
- The same query works when the catalog grows from 60 to 10,000 tools; the
  traversal is anchored at both ends and capped.
- The graph is a live store, not a build artifact. Adding a server updates it.

The demo measures the graph against BM25 and uniform weights. If the graph did
not matter, the numbers would show it.

## 16. Risks and kill criteria

| Risk | Likelihood | Response |
|---|---|---|
| No four-tool chain in the fixture | medium | G2 kill criterion. Fix the fixture or drop the ordering claim |
| "This is tool RAG" | certain to be said | BM25 baseline and uniform graph rows |
| Gateway adds a round trip | certain | Report latency; the context saving is the point |
| Real servers have no output types | high | Fixtures declare them; README states the limitation |
| Learning curve is flat | medium | Add ambiguous routes and distractors; if still flat, report uniform and drop the curve |
| stdio logs corrupt protocol | medium | All logs to stderr; integration test with a real client |
| Time | high | Cut HTTP, write refusal, reuse, adversarial set |

The one number that decides the project: does a four-tool chain exist where each
step needs the previous step's output. Test it before building the planner.

## 17. Open questions

1. Does the landing page need a hosted gateway URL, or is the Loom enough? The
   current answer is the Loom plus a one-command local run.
2. Do we keep plan reuse in P0? It is a strong second beat, but it is not
   required for the counters or the learning curve.
3. Does the write refusal earn its place, or is it a distraction from the
   context and learning claims?

Resolve 2 and 3 at G4, not before.
