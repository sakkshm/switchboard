# Switchboard

One MCP gateway for every tool. The agent asks for a task and gets a four-step
plan, not a sixty-tool catalog.

Status: HydraDB OSS + MinIO backend is verified. The CLI, gateway, fixtures, and
evals are being built for the Nov 16 submission. Commands below are the target
interface, not a finished release.

## The problem

Tools give a model hands. Every tool it might use is described in its context on
every turn, and it picks by similarity, not by correctness. Four things go wrong:

1. **Bloat.** 60 tools at roughly 120 tokens each is about 7,000 tokens per
   turn, to use maybe 4.
2. **Wrong order.** Tool B needs an ID tool A produces. The model discovers
   this by trying, failing, and retrying, one round trip per attempt.
3. **No memory.** The sequence dies with the session and is rediscovered next
   time.
4. **No boundary.** The model sees the whole catalog, including the tools that
   delete things.

## What it does

Switchboard is a local MCP gateway. You add upstream MCP servers once with a
CLI, and every MCP client on the machine can use the same gateway.

**Once, at setup.** `switchboard index` asks each upstream server for
`tools/list`, reads the descriptions and schemas, and writes a graph into
HydraDB: tools, the types they consume and produce, and an edge between any two
tools where one produces what the other consumes.

**At request time.**

1. The agent calls `find` with a task.
2. The gateway matches the task to a goal tool and a small candidate set. Pure
   BM25 over the discovered tools. No model call.
3. It runs one graph query: all paths that end at the goal tool, capped at six
   hops.
4. The returned node order is the plan. It is checked, not guessed: every step
   must consume a type produced by an earlier step.
5. The agent calls `run` with the plan id and input values. The gateway executes
   the chain itself, filling each step's arguments from the input or a previous
   step's output.
6. A successful run raises the edge weights it used; a failed step lowers them.

The full upstream catalog never enters the model's window.

## What the client sees

| | Four MCP servers, direct | Switchboard |
|---|---|---|
| MCP servers the client configures | 4 | **1** |
| Tool schemas in context | 60 | **2** |
| Tools named in the plan | 0 | **4** |
| Order comes from | model guess | **graph path** |
| Learned edge weights | none | **HydraDB** |
| Works in any MCP client | yes | **yes** |

The client sees two tools, `find` and `run`. The four is the length of the plan
returned by `find`, not four schemas in the tool list. That distinction is in
the pitch on purpose: a gateway cannot advertise a task-specific tool list
before it has the task.

Baseline and gateway run the same task in OpenCode:

- **Baseline:** OpenCode connects to the four fixture servers directly and sees
  60 native tools.
- **Gateway:** OpenCode connects to `switchboard serve` and sees 2 tools. `find`
  returns the same 4-step chain, and `run` executes it.

The eval table reports tool schemas, tokens, graph calls, order validity, task
success, and the learning curve. No web UI is required to see the difference.

## Install

```bash
cp .env.example .env
docker compose up -d                 # HydraDB OSS 0.4.0 + MinIO

python -m venv .venv
. .venv/bin/activate
pip install -e .

switchboard add mcp issues -- python -m tests.fixtures.issue_server
switchboard add mcp repo -- python -m tests.fixtures.repo_server
switchboard add mcp incident -- python -m tests.fixtures.incident_server
switchboard add mcp account -- python -m tests.fixtures.account_server

switchboard list
switchboard index
switchboard plan "comment on the failing test issue in acme/api"
switchboard serve                    # stdio, one client
switchboard serve --http 127.0.0.1:8765   # daemon, many clients
```

Config lives in `~/.config/switchboard/config.json`. It contains upstream
commands and URLs, not a tool catalog. Tools are discovered at `index` and at
server startup. Secrets in headers stay as `{env:NAME}` references.

OpenCode client config for the stdio gateway:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "switchboard": {
        "type": "local",
        "command": ["switchboard", "serve"]
      }
    }
  }
}
```

Any MCP client works the same way. For the HTTP daemon, point a remote MCP
client at `http://127.0.0.1:8765/mcp`.

## MCP surface

Exactly two tools. The agent never sees the upstream catalog.

| Tool | Input | Returns |
|---|---|---|
| `find` | `task`, optional `inputs` | plan id, four ordered steps, missing inputs |
| `run` | `plan_id`, optional `inputs` | one result per step, or the failing step |

There is no fallback catalog and no `describe` escape hatch. If the graph has no
path, `find` returns `NoPlan`. That is the removal test.

## How HydraDB is used

The graph is the product, not a cache in front of one.

```
(:Tool {id, key, name, server, consumes, produces, writes})
(:Tool)-[:FEEDS {weight}]->(:Tool)
(:Plan {id, key, task, hash, wins})
(:Plan)-[:STEP {i, args}]->(:Tool)
```

One traversal per plan:

```cypher
CALL algo.MSpaths({
  sourceLabel: 'Tool', sourceProperty: 'key', sourceValues: [...],
  targetLabel: 'Tool', targetProperty: 'key', targetValues: [goal],
  relTypes: ['FEEDS'], relDirection: 'outgoing',
  maxLen: 6, pathCount: 10, pairwise: true, resultLimit: 50
}) YIELD path RETURN path
```

Candidate keys are validated and inlined; the OSS planner does not accept a
parameter list there. The path is the plan. Python scores paths by candidate
coverage, hop count, and learned weight; it does not sort tools by hand, because
order is a property of the data.

Two independent checks must agree: the path exists in the graph, and the types
line up. If either fails, there is no plan.

**Removal test.** With the graph empty, `find` returns `NoPlan`. There is no
fallback catalog and no local ordering heuristic. That is a test in CI, not a
claim in a paragraph.

**Transport.** HydraDB OSS 0.4.0, started with Docker Compose next to MinIO.
One synchronous `POST /v1/graphs/default/query`; results are typed cells,
decoded in the store client. MinIO is required: the local filesystem store does
not implement the updates that plans and edge weights need.

**Learning loop.** A successful run raises `FEEDS.weight` on the edges it used;
a failed step lowers them. The planner scores paths by candidate coverage plus
weight, and the eval reports top-1 plan accuracy against a BM25 baseline and a
uniform-weight graph over 5 rounds with a holdout.

**What we do not do with it.** No entity resolution, no dedupe, no merging
records from different sources, no vector search over the catalog.

## What is real and what is mocked

| Piece | Status |
|---|---|
| HydraDB graph, traversal, plans, edge weights | real, OSS 0.4.0 |
| CLI, gateway, planner, binder, executor | real |
| 60-tool catalog | synthetic, our four fixture MCP servers |
| Capability headers (`consumes` / `produces`) | our convention; real servers often lack output types |
| Order validity and learning curve | measured on the fixture, with a baseline |
| OpenCode baseline and gateway runs | real MCP sessions |
| Auth, multi-tenancy, production hardening | not built. Stated as out of scope |
| Landing page | static, no backend |

## Architecture

```
Any MCP client (OpenCode, Claude Desktop, Cursor, your own agent)
    |  stdio: switchboard serve
    |  or HTTP: http://127.0.0.1:8765/mcp
    v
Switchboard MCP server
    |  tools/list -> find, run
    |-- matcher    BM25 over discovered tools
    |-- planner    one /v1/graphs/default/query -> ordered path
    |-- validator  consumes/produces check
    |-- binder     $input / $steps.N.result
    +-- executor   upstream tools/call, timeouts, output caps
    |
    v
HydraDB OSS 0.4.0  (Docker Compose)
    |  POST /v1/graphs/default/query
    v
MinIO (S3-compatible)

N upstream MCP servers (fixtures, filesystem, git, remote HTTP)
```

## Non-goals

Entity resolution, cross-source identity, agent frameworks, a vector database,
graph visualisation, auth, SSO, multi-tenant isolation, autoscaling, Kubernetes.
Each one is a product on its own and none of them makes the demo better.

## Docs

- `docs/SPEC.md`: the product, the schema, the evals, the risks
- `docs/PLAN.md`: the schedule, the gates, the pre-decided cut list
- `docs/TESTING.md`: verified backend runbook, query cookbook, traps
