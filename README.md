# Switchboard

An MCP gateway that gives an agent the four tools that chain, in order, instead
of the whole catalog.

---

## The problem

Tools give a model hands. But every tool it might use is described in its
context on every turn, and it picks by similarity, not by correctness. Four
things go wrong:

1. **Bloat.** 60 tools at roughly 120 tokens each is about 7,000 tokens per
   turn, to use maybe 4.
2. **Wrong order.** Tool B needs an ID tool A produces. The model discovers
   this by trying, failing, and retrying, one round trip per attempt.
3. **No memory.** The sequence dies with the session and is rediscovered next
   time.
4. **No boundary.** The model sees the whole catalog, including the tools that
   delete things.

## What it does

Switchboard sits between one agent and N MCP servers.

**Once, at setup.** It asks each server for `tools/list`, reads the schemas, and
writes a graph into HydraDB: tools, the types they consume and produce, and an
edge between any two tools where one produces what the other consumes.

**At request time.**

1. The agent sends a task and its identity.
2. The gateway matches the task to a goal tool and a small candidate set. Pure
   BM25 over the catalog. No model call.
3. It runs one Cypher query: all paths that end at the goal tool, capped at six
   hops.
4. The returned node order is the plan. It is checked, not guessed: every step
   must consume a type produced by an earlier step.
5. The gateway executes the plan itself, filling each step's arguments from
   user input or from a previous step's output.
6. On success it stores the plan, so the next similar task is a lookup instead
   of a traversal.

The full catalog never enters the model's window.

## What you see

The demo page runs the same task twice, side by side.

| | Preload all | Switchboard |
|---|---|---|
| Tools in context | 60 | **4** |
| Tokens per turn | about 7,000 | about 900 |
| Graph calls | none | **1, then 0 on reuse** |
| Order validity | 0.62 (target to measure) | **1.00** |
| A write when the agent is read-only | allowed | **refused at the executor** |

Below the counters: the chain, `Repo -> Issue -> Comment -> Slack`, one box per
step, and the executed trace. A blocked step turns red.

The left column is labeled static cost, measured: the token count of the full
catalog is exact and computed. Its order-validity number comes from the eval
run, not from a live agent.

## How an agent uses it

One line in the MCP config. The agent sees three tools instead of sixty:

| Tool | Input | Returns |
|---|---|---|
| `find` | task text | matching tool names and one-line descriptions |
| `describe` | tool name | that one tool's JSON schema |
| `run` | plan id, input args | executes the plan through the proxy |

`find` and `describe` exist so the agent can improvise when no plan matches,
without ever loading the catalog. `run` is the proxy: the agent never holds a
schema, and the gateway binds each step's arguments.

## How HydraDB is used

The graph is the product, not a cache in front of one.

```
(:Tool)-[:CONSUMES]->(:Type)
(:Tool)-[:PRODUCES]->(:Type)
(:Tool)-[:FEEDS]->(:Tool)
(:Plan)-[:STEP {i}]->(:Tool)
(:Agent)-[:GRANTED]->(:Tool)
(:Decision {agent, tool, verdict, reason, ts})
```

One query per plan:

```cypher
MATCH p = (entry:Tool)-[:FEEDS*1..6]->(goal:Tool {key: $goal})
WHERE entry.key IN $candidates
RETURN [n IN nodes(p) | n.key] AS chain,
       [n IN nodes(p) | n.name] AS names,
       length(p) AS hops
ORDER BY hops ASC
LIMIT 25
```

The path is the plan. Python scores paths by candidate coverage and picks one;
it does not sort tools by hand, because order is a property of the data.

Two independent checks must agree: the path exists in the graph, and the types
line up. If either fails, there is no plan.

**Removal test.** With the collection empty, `plan()` raises `NoPlan`. There is
no fallback catalog and no local ordering heuristic. That is a test in CI, not a
claim in a paragraph.

**Transport.** `POST /byog/query` on HydraDB Cloud. No self-hosted engine, no
Docker, no async indexing. A plan is one synchronous call that returns row
dicts.

**Second use, P1.** When BM25 confidence is low, the gateway asks
`client.query()` over ingested tool docs and past failure notes to pick the goal
tool. That is the one thing a pure graph database cannot do, and the honest
answer to "why HydraDB and not Neo4j."

**What we do not do with it.** No entity resolution, no dedupe, no merging
records from different sources, no vector search over the catalog. Storing a
step's result is keyed by `(tool, hash(args))` and combined with nothing.

## What is real and what is mocked

| Piece | Status |
|---|---|
| HydraDB graph, traversal, plans | real |
| Gateway, planner, binder, executor | real |
| 60-tool catalog | synthetic, our fixture server. Real servers can be added |
| Filesystem and git MCP servers | real, no auth |
| Order-validity numbers | measured on the golden set, 3 reps |
| 100x scale test | synthetic, labeled as such |
| Auth, multi-tenancy, deployment | not built. Stated as out of scope |
| LLM baseline | optional, offline, never in the demo path |

## Architecture

```
MCP client (opencode, Claude, any agent)
    |  find / describe / run
    v
Switchboard (FastAPI + MCP server)
    |-- matcher    BM25 over catalog.json
    |-- planner    one /byog/query -> ordered path
    |-- validator  consumes/produces check
    |-- binder     $input / $steps.N.result
    |-- executor   tools/call, grants, timeouts, audit
    +-- trace      /trace/{id}, /metrics, /evals
    |
    v
HydraDB Cloud  POST /byog/query
    ^
    |
N MCP servers (fixture, filesystem, git)
```

## Quickstart

```bash
cp .env.example .env          # HYDRADB_API_KEY
pip install -r requirements.txt
python scripts/probe.py       # six checks against the cloud, fails loudly
python scripts/seed.py        # idempotent, safe to rerun
make dev                      # http://localhost:8000
python evals/run.py --reps 3
```

## Non-goals

Entity resolution, cross-source identity, agent frameworks, a vector database,
graph visualisation, auth, SSO, multi-tenant isolation, autoscaling, Kubernetes.
Each one is a product on its own and none of them makes the demo better.

## Docs

- `SPEC.md`: the product, the schema, the evals, the risks
- `PLAN.md`: the schedule, the five gates, the pre-decided cut list
- `TESTING.md`: pre-build checks, the backend decision, the verified local runbook
