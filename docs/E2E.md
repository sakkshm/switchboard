# End-to-end runbook

Every feature, in order, from a clean checkout. Run from the repo root with the
venv built (`python -m venv .venv && .venv/bin/pip install -e ".[dev]"`). With
the venv active, drop the `.venv/bin/` prefix from every command below.

Two tracks prove the same code path:

- **Track A, fixtures.** Fast and deterministic. Proves the planner, the
  learning curve, and the MCP surface. 60 synthetic tools.
- **Track B, real servers.** No mocks. Filesystem, fetch, and GitHub's own MCP
  server. 46 real tools.

`SWITCHBOARD_CONFIG` picks the graph to talk to. Steps 1-8 use the fixture, step
9 uses the real servers, step 10 is the client.

## 1. Stack up

```bash
cp .env.example .env
docker compose up -d --wait
.venv/bin/switchboard status
# HydraDB http://127.0.0.1:18443  ready=True
# servers=4 tools=... edges=... plans=...
```

## 2. Fast checks, no graph changes

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
# 14 passed
```

Covers the removal test (`NoPlan` on an empty graph), the binder, the capability
annotation path, the structural/inferred capability layers, and the executor's Go
file-read unwrap.

## 3. CLI config surface

```bash
export SWITCHBOARD_CONFIG=/tmp/opencode/e2e-cli.json
.venv/bin/switchboard add mcp echo -- .venv/bin/python -m tests.fixtures.echo_server
.venv/bin/switchboard list
.venv/bin/switchboard remove mcp echo

.venv/bin/switchboard add mcp ctx --url https://mcp.context7.com/mcp
.venv/bin/switchboard remove mcp ctx
```

Expect `added echo: 2 tools`, the server line, `removed echo`, then
`added ctx: 2 tools`. Both transports are probed with a real `tools/list`. The
config file holds the command only, never a secret.

## 4. Index, tools, graph

```bash
export SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json
.venv/bin/switchboard reset --yes
.venv/bin/switchboard index
.venv/bin/switchboard index          # idempotent
.venv/bin/switchboard status
.venv/bin/switchboard tools | tail -2
.venv/bin/switchboard graph | tail -2
```

Expect `indexed 60 tools, 69 FEEDS edges` on both runs (no duplicates, weights
untouched), `servers=4 tools=60 edges=69`, `60 tools`, `69 edges`.

## 5. Plan and run

On a freshly reset graph every edge weighs 1.0, so the first plan for this task
takes the **trap route**: `repo.guess_repo` (a tool that declares the same
consumes/produces as `repo.repo_search` but always fails). That is the point of
the fixture - a plausible route the graph cannot tell from the real one until it
has run.

```bash
out=$(.venv/bin/switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced)
echo "$out"
# 0. repo.guess_repo   ...   <- trap
.venv/bin/switchboard run "$(echo "$out" | awk 'NR==1{print $2}')" \
  --input repo=acme/api --input message=reproduced
# ok: false, step 0 raises "broken tool: guess_repo"
```

Run the same task a few times (step 6) and the planner switches to the real
route, then succeeds:

```bash
out=$(.venv/bin/switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced)
# 0. repo.repo_search -> repo.list_issues -> issue.get_issue -> issue.add_comment
.venv/bin/switchboard run "$(echo "$out" | awk 'NR==1{print $2}')" \
  --input repo=acme/api --input message=reproduced
# "ok": true, ending in
# comment on acme/api#42: failing test in checkout: reproduced
```

Also check the missing-input path:

```bash
.venv/bin/switchboard plan "comment on the failing test issue in acme/api"
# ... missing: repo, message
```

## 6. Learning loop

The trap and the real first hop both feed `repo.list_issues`. After a reset both
edges weigh 1.0; running the task moves them apart.

```bash
.venv/bin/switchboard graph | grep -E 'guess_repo -> repo.list_issues|repo_search -> repo.list_issues'
# repo.guess_repo  -> repo.list_issues  weight=1.0
# repo.repo_search -> repo.list_issues  weight=1.0
# run the plan from step 5 until it succeeds (a few rounds)
.venv/bin/switchboard graph | grep -E 'guess_repo -> repo.list_issues|repo_search -> repo.list_issues'
# repo.guess_repo  -> repo.list_issues  weight=0.6   (failed, decayed)
# repo.repo_search -> repo.list_issues  weight=2.0   (succeeded, at cap)
```

A successful run raises each used `FEEDS.weight` by 0.2 (cap 2.0); a failed step
lowers it by 0.2. Once `repo.repo_search` outranks `repo.guess_repo`, the planner
routes to it. The weight lives in HydraDB, not in code.

## 7. MCP surface, stdio and HTTP

```bash
.venv/bin/python evals/mcp_check.py
```

Expect 7 `PASS` lines and `OK: 0 failed`: `tools/list` is exactly `find` and
`run` over stdio, and over an HTTP daemon with two concurrent clients, and all
three `find` + `run` round-trips return `ok: true`.

## 8. Eval harness and the removal test

```bash
.venv/bin/python evals/chain_check.py        # six real four-step chains
.venv/bin/python evals/run.py --reps 3 --rounds 5
.venv/bin/python evals/baseline_tokens.py
```

`run.py` rewrites `evals/results/latest.json` (git-ignored unless it is the
committed file): BM25 fair 0.08 and BM25 naive 0.00, uniform graph 0.33, learned
graph 1.00 from round 1, stable to round 5, zero spread across 3 replicates.
`baseline_tokens.py` prints the 32.3x schema-token reduction.

```bash
.venv/bin/switchboard reset --yes
.venv/bin/switchboard plan "comment on the failing test issue in acme/api"
# switchboard: NoPlan: the tool graph is empty; run switchboard index
```

## 9. Real servers, no fixtures

```bash
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/config.json
export GITHUB_TOKEN=$(gh auth token)
mkdir -p /tmp/opencode/real-demo
docker pull ghcr.io/github/github-mcp-server:latest

.venv/bin/switchboard add mcp filesystem -- npx -y @modelcontextprotocol/server-filesystem /tmp/opencode/real-demo
.venv/bin/switchboard add mcp fetch -- uvx mcp-server-fetch
.venv/bin/switchboard add mcp github --env 'GITHUB_PERSONAL_ACCESS_TOKEN={env:GITHUB_TOKEN}' -- \
  docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_TOOLSETS=repos,issues \
  ghcr.io/github/github-mcp-server

.venv/bin/switchboard reset --yes
.venv/bin/switchboard annotate --infer     # or declare the edges by hand
.venv/bin/switchboard index
.venv/bin/switchboard graph
```

`annotate --infer` asks a model which tools return a payload another tool can
take; layer 1 already takes required inputs as `consumes`. It finds the `fetch`
branch even though the fetch server declares no output schema, and adds the
read/write and search/write routes it can justify (a couple of dozen edges). A
tool whose essential parameter is optional in its schema (GitHub's
`get_file_contents` needs `path`) still needs one manual `annotate` to bind it.
To declare a precise four-edge diamond by hand instead:

```bash
.venv/bin/switchboard annotate fetch fetch --consumes url --produces content
.venv/bin/switchboard annotate github get_file_contents --consumes owner,repo,path --produces content
.venv/bin/switchboard annotate filesystem write_file --consumes path,content
.venv/bin/switchboard annotate filesystem append_file --consumes path,content
.venv/bin/switchboard index
.venv/bin/switchboard graph
```

That gives 46 tools and 4 edges: `fetch.fetch` feeds both `filesystem.write_file`
and `filesystem.append_file`, and `github.get_file_contents` feeds both, all
`weight=1.0`. Both paths keep the load-bearing `fetch.fetch -> write_file` edge.

```bash
rm -f /tmp/opencode/real-demo/example.html /tmp/opencode/real-demo/log.txt

out=$(.venv/bin/switchboard plan "fetch a web page and write it to a local file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html)
.venv/bin/switchboard run "$(echo "$out" | awk 'NR==1{print $2}')" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html
wc -c /tmp/opencode/real-demo/example.html      # 208, live from example.com

out=$(.venv/bin/switchboard plan "fetch a web page and append it to a local log file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/log.txt)
.venv/bin/switchboard run "$(echo "$out" | awk 'NR==1{print $2}')" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/log.txt
wc -c /tmp/opencode/real-demo/log.txt           # 208, appended

out=$(.venv/bin/switchboard plan "download README.md from a GitHub repository and write it to a local file" \
  --input owner=sakkshm --input repo=switchboard --input path=README.md)
.venv/bin/switchboard run "$(echo "$out" | awk 'NR==1{print $2}')" \
  --input owner=sakkshm --input repo=switchboard --input path=README.md
# github.get_file_contents -> filesystem.write_file, live from the GitHub API

.venv/bin/switchboard graph
# the used edge weight climbs 1.0 -> 1.6 over three runs
```

Both sources produce `content`, so the same goal is reachable from `fetch` and
from `github`. The planner prefers the route whose inputs you supplied: a `url`
selects `fetch`, an `owner`/`repo` selects GitHub.

The same MCP check works on the real graph:

```bash
.venv/bin/python evals/mcp_check.py "fetch a web page and write it to a local file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/e2e.html
```

## 10. Client, visible in the TUI

OpenCode reads project config from the git root. Drop one harness config there:

```bash
cp evals/harness/gateway/opencode.json opencode.json        # fixture
# or
cp evals/harness/gateway/opencode.real.json opencode.json   # real servers
export GITHUB_TOKEN=$(gh auth token)                        # real only
opencode
```

In the TUI ask for the task. Expect exactly two tools
(`switchboard_find`, `switchboard_run`), a numbered plan, then a run result. The
debug line `mcp connected server=switchboard tools=2` confirms it.

Headless equivalent, for a log you can paste:

```bash
OPENCODE_DISABLE_AUTOUPDATE=1 opencode --standalone run --auto --format json \
  -m opencode/deepseek-v4.1-flash-free \
  "Task: comment on the failing test issue in acme/api. Inputs: repo=acme/api, message=reproduced. Use the switchboard find tool, then switchboard run with the plan_id and the same inputs."
```

Remove `opencode.json` when done (it is git-ignored).

## 11. Pass criteria

| Feature | Check | Step |
|---|---|---|
| Graph store up | `status` shows `ready=True` | 1 |
| Unit tests, lint | `ruff` clean, `pytest` 14 passed | 2 |
| `add` / `list` / `remove`, both transports | tool counts printed | 3 |
| `index`, idempotent | `60 tools, 69 edges` twice | 4 |
| `tools`, `graph` | counts and weights listed | 4 |
| `plan`, multi-step order | four-step chain | 5 |
| `plan`, missing inputs | `missing: ...` | 5 |
| `run`, failure on the trap | trap route fails, edge decays | 5 |
| `run`, success after learning | `ok: true` | 5, 6 |
| Learning loop | real edge 2.0, trap edge 0.6 | 6 |
| MCP surface, stdio | exactly `find`, `run` | 7 |
| MCP surface, HTTP, concurrent | two clients both `ok` | 7 |
| Removal test | `NoPlan` on empty graph | 8 |
| Learning curve, baseline | BM25 fair 0.08 / naive 0.00 / uniform 0.33 / learned 1.00 | 8 |
| Token reduction | 32.3x | 8 |
| `annotate` | real tools get edges | 9 |
| `annotate --infer` | model names produced types, `fetch` branch appears | 9 |
| Real branch, no mocks | `fetch` feeds `write_file` and `append_file`; live page, log, and GitHub file written | 9 |
| Client TUI | 2 tools, plan, run | 10 |

Full logs and the OpenCode 60-tool baseline caveat are in `docs/TESTING.md` and
`docs/RESULTS.md`.
