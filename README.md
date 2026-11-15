# Switchboard

One MCP gateway for every tool. The agent asks for a task and gets a four-step
plan, not a sixty-tool catalog.

Status: verified on 2026-11-15 against HydraDB OSS 0.4.0 + MinIO, OpenCode
v2.2.5, and two concurrent HTTP MCP clients. The CLI, gateway, fixtures, and
evals are in this repo. Results and caveats: `docs/RESULTS.md`.

## The problem

Tools give a model hands. Every tool it might use is described in its context on
every turn, and it picks by similarity, not by correctness. Four things go wrong:

1. **Bloat.** 60 tools at roughly 90 tokens each is about 5,500 tokens per
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
| Tool schemas in the client's MCP layer | 60 | **2** |
| Tool-schema tokens at the MCP layer | 5,453 | **169** |
| Tools named in the plan | 0 | **4** |
| Order comes from | model guess | **graph path** |
| Learned edge weights | none | **HydraDB** |
| Works in any MCP client | yes | **yes** |

The client sees two tools, `find` and `run`. The four is the length of the plan
returned by `find`, not four schemas in the tool list. That distinction is in
the pitch on purpose: a gateway cannot advertise a task-specific tool list
before it has the task.

Baseline and gateway in OpenCode:

- **Gateway, verified:** OpenCode connects to `switchboard serve` and sees 2
  tools. The model called `find` for "comment on the failing test issue in
  acme/api", got a 4-step plan, called `run`, and got `ok: true` with four
  results ending in the posted comment.
- **Baseline, blocked by the client:** the four fixture servers connect with 60
  tools total, but OpenCode v2.2.5 did not put them in the model's tool catalog
  even with `codemode: false`. The 5,453-vs-169 token number is therefore
  measured from real MCP `tools/list` payloads, not from a live 60-tool model
  run. Details and the failed workarounds are in `docs/RESULTS.md`.

The eval table reports tool schemas, tokens, order validity, task success, and
the learning curve. No web UI is required to see the difference.

## Results

24 tasks, 18 train / 6 holdout, 3 replicates, 5 rounds:

| Mode | Holdout success | Order validity |
|---|---|---|
| BM25, top-4 relevance (naive) | 0.00 | 0.00 |
| BM25, goal + producer + dependency order (fair) | 0.08 | 0.96 |
| Uniform graph | 0.33 | 1.00 |
| Learned graph, round 1 | 1.00 | 1.00 |
| Learned graph, round 5 | 1.00 | 1.00 |

Two BM25 baselines, so the delta is not a strawman. The naive one is the top-4
tools by relevance, run in that order. The fair one uses BM25 for goal selection,
picks the best-ranked producer for each missing input type, and orders the chain
by data dependency - retrieval plus ordering, no graph and no weights. It still
scores 0.08: BM25 names the right goal first for every task, but it fills the
rest of the chain with relevance neighbours, not producers.

The graph supplies order. The learned `FEEDS` weights pick the route: four
fixture families contain a trap sibling with identical types that fails at
execution, and uniform weights take it 4 times out of 6 on the holdout. One
training round moves the weights and the holdout goes to 6/6. Full tables,
latency, and the A/B caveat: `docs/RESULTS.md`. Raw: `evals/results/`.

## Install

```bash
cp .env.example .env
docker compose up -d                 # HydraDB OSS 0.4.0 + MinIO

./install.sh                         # venv + editable install + `switchboard` on PATH
switchboard status                   # HydraDB ready=True

switchboard add mcp issue -- .venv/bin/python -m tests.fixtures.issue_server
switchboard add mcp repo -- .venv/bin/python -m tests.fixtures.repo_server
switchboard add mcp incident -- .venv/bin/python -m tests.fixtures.incident_server
switchboard add mcp account -- .venv/bin/python -m tests.fixtures.account_server

switchboard list
switchboard index
switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced
switchboard serve                    # stdio, one client
switchboard serve --http 127.0.0.1:8765   # daemon, many clients
```

`install.sh` builds `.venv`, installs this repo editable, and symlinks the
`switchboard` command into `~/.local/bin` (override with `BIN_DIR=... ./install.sh`).
If that directory is not on your `PATH`, it prints the one `export` line to add.

The fixture servers above are launched with `.venv/bin/python`, so they work
without activating. Activate the venv (`. .venv/bin/activate`) if you prefer bare
`python` and `pytest`.

Doing it by hand instead:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"    # the venv's pip, not the system one
ln -s "$PWD/.venv/bin/switchboard" ~/.local/bin/switchboard
```

Two install traps:

- `pip install -e ".[dev]"` with the **system** pip fails on Debian/Ubuntu with
  `error: externally-managed-environment` (PEP 668). That means the venv is not
  active - use `.venv/bin/pip`, or `pipx install -e .` / `uv tool install -e .`.
- Do **not** run `pip install switchboard`. That name on PyPI is an unrelated
  package (a Pyramid feature flipper). This project is installed from the repo,
  with `-e`.

Config lives in `~/.config/switchboard/config.json`. It contains upstream
commands and URLs, not a tool catalog. Tools are discovered at `index` and at
server startup. Secrets in headers stay as `{env:NAME}` references.

### Real servers, no fixtures

The same flow runs against real MCP servers. This is the tested set
(2026-11-15): `filesystem` (npx), `fetch` (uvx), and the official `github`
Docker server.

```bash
switchboard add mcp filesystem -- npx -y @modelcontextprotocol/server-filesystem /tmp/opencode/real-demo
switchboard add mcp fetch -- uvx mcp-server-fetch
switchboard add mcp github --env 'GITHUB_PERSONAL_ACCESS_TOKEN={env:GITHUB_TOKEN}' -- \
  docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_TOOLSETS=repos,issues \
  ghcr.io/github/github-mcp-server

switchboard reset --yes          # drop the fixture graph first
switchboard index                # 46 tools; edges only where a capability is declared
switchboard tools                # every indexed tool and its types
switchboard graph                # every FEEDS edge and its learned weight
```

MCP publishes no dependency types, so a real tool has no edges until the gateway
learns what it consumes and produces. Let a model infer them, one command for
every server:

```bash
switchboard annotate --infer     # layer 1 reads the schemas, layer 2 names the data types
switchboard index                # the same branch, discovered instead of declared
```

`--infer` takes each tool's required input parameters as `consumes`, then asks a
model which tools return a payload another tool can take (`produces`). It finds
the `fetch` branch, which no output schema reveals, with no hand annotation. A
tool whose essential parameter is optional in its schema (GitHub's
`get_file_contents` needs `path`) still needs one manual `annotate` to bind it.
The backend is the local `opencode` CLI; `SWITCHBOARD_LLM_CMD` replaces the
command and `SWITCHBOARD_LLM_MODEL` sets the model. Precedence is header, then
manual `annotate`, then inferred.

Or declare the types by hand:

```bash
switchboard annotate fetch fetch --consumes url --produces content
switchboard annotate github get_file_contents --consumes owner,repo,path --produces content
switchboard annotate filesystem write_file --consumes path,content
switchboard annotate filesystem append_file --consumes path,content
```

Either way the graph gets a real branch: `fetch.fetch` feeds both `write_file`
and `append_file` (4 edges in all). `plan` then `run` execute the declared chains
against the live services:

```bash
switchboard plan "fetch a web page and write it to a local file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html
switchboard run <plan_id> --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html

switchboard plan "fetch a web page and append it to a local log file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/log.txt
switchboard run <plan_id> --input url=https://example.com --input path=/tmp/opencode/real-demo/log.txt

switchboard plan "download README.md from a GitHub repository and write it to a local file" \
  --input owner=sakkshm --input repo=switchboard --input path=README.md
switchboard run <plan_id> --input owner=sakkshm --input repo=switchboard --input path=README.md
```

Two servers both produce `content`, so `fetch.fetch` and `github.get_file_contents`
can both reach the same goal. The planner breaks that tie toward the route whose
inputs the caller supplied: with a `url` it picks `fetch`, with `owner`/`repo` it
picks GitHub. Each successful run raises the weight on the edges it used.
`docs/TESTING.md` has the full transcript.

OpenCode client config for the stdio gateway. The tested variant is
`evals/harness/gateway/opencode.json`: copy it to the repo root as
`opencode.json` so OpenCode picks it up from the git root.

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
fallback catalog and no local ordering heuristic. A unit test covers it.

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
| Capability headers (`consumes` / `produces`) | our convention; MCP publishes none, so real servers declare them with `switchboard annotate`, or `annotate --infer` reads the schemas and asks a model |
| Order validity and learning curve | measured on the fixture, with a baseline |
| Real servers (filesystem, fetch, github) | real MCP sessions; `find` + `run` wrote a fetched page and a live GitHub README to disk |
| OpenCode gateway run | real MCP session, 2 tools, `find` + `run` succeeded |
| OpenCode 60-tool baseline | blocked in v2.2.5; token cost measured at the MCP layer |
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

## Landing page

The static landing page is built into `site/`, with React/TypeScript source in
`frontend/`. To develop it, run `npm ci` then `npm run dev` inside `frontend/`.
Run `npm test` and `npm run build` before committing source and generated output.
For a production preview from the repository root:

```bash
python -m http.server 8080 --directory site
```

See `frontend/README.md` for the build and artwork details.

## Docs

- `docs/E2E.md`: end-to-end runbook, every feature with expected output
- `docs/DEMO.md`: demo script - commands to run and show, fixture and real, plus the TUI
- `docs/RESULTS.md`: measured curve, token cost, trap-route effect, A/B caveat
- `docs/SPEC.md`: the product, the schema, the evals, the risks
- `docs/PLAN.md`: the schedule, the gates, the pre-decided cut list
- `docs/TESTING.md`: verified backend runbook, query cookbook, traps
- `docs/LOOM.md`: demo recording script
- `docs/SOCIAL.md`: submission post drafts
