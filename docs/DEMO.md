# Demo script

Everything to run and show, in order. Three tracks: fixture MCP servers
(deterministic), real MCP servers (no mocks), and the OpenCode TUI on top of
either. All numbers below were re-run on 2026-11-15.

Set `SB=.venv/bin/switchboard` once, or run `./install.sh` first and use bare
`switchboard`.

## 0. Prep, once

```bash
cd switchboard
cp .env.example .env
docker compose up -d --wait          # HydraDB OSS 0.4.0 + MinIO
./install.sh                         # venv + editable install + switchboard on PATH
switchboard status                   # HydraDB http://127.0.0.1:18443  ready=True

docker pull ghcr.io/github/github-mcp-server:latest
export GITHUB_TOKEN=$(gh auth token)          # needed only for the real track
```

The two tracks share the single `default` graph, so every track starts with
`switchboard reset --yes`.

## 1. Track A - fixture MCP servers (deterministic, 60 tools)

Four small MCP servers in `tests/fixtures/`. Fast, no network, no model.

```bash
export SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json
switchboard list                     # 4 upstreams configured (tool counts fill in after index)
switchboard reset --yes
switchboard index                    # indexed 60 tools, 69 FEEDS edges
switchboard graph | head -5          # every edge and its weight
switchboard tools | tail -3
```

Show the plan. On a fresh graph the first plan takes the trap route on purpose:

```bash
switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced
```

```
plan <id>  goal issue.add_comment
  task: comment on the failing test issue in acme/api
  0. repo.guess_repo            repo <- input          <- the trap
  1. repo.list_issues           repo_meta <- repo.guess_repo
  2. issue.get_issue            issue_number <- repo.list_issues
  3. issue.add_comment          issue <- issue.get_issue, message <- input
inputs: repo, message
run: switchboard run <id> --input repo=acme/api --input message=reproduced
```

Run it and watch the edge decay:

```bash
switchboard run <id> --input repo=acme/api --input message=reproduced
#   fail 0. repo.guess_repo   Error executing tool guess_repo - RuntimeError: broken tool: guess_repo
# stopped at step 0 of 4: repo.guess_repo
# that edge lost 0.2 weight; run `switchboard plan "..."` again

switchboard graph | grep -E 'guess_repo -> repo.list_issues|repo_search -> repo.list_issues'
# repo.guess_repo  -> repo.list_issues  weight=0.8
# repo.repo_search -> repo.list_issues  weight=1.0
```

Plan again: the graph now routes around the trap, and the run succeeds.

```bash
switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced
#   0. repo.repo_search -> repo.list_issues -> issue.get_issue -> issue.add_comment

switchboard run <new-id> --input repo=acme/api --input message=reproduced
#   ok   0. repo.repo_search           acme/api@main
#   ok   1. repo.list_issues           42
#   ok   2. issue.get_issue            acme/api#42: failing test in checkout
#   ok   3. issue.add_comment          comment on acme/api#42: failing test in checkout: reproduced
# done: 4/4 steps ok
```

Removal test - the graph is load-bearing, there is no fallback catalog:

```bash
switchboard reset --yes
switchboard plan "comment on the failing test issue in acme/api"
# switchboard: NoPlan: the tool graph is empty; run switchboard index
```

## 2. Track B - real MCP servers (no mocks, 46 tools)

`filesystem` (npx), `fetch` (uvx), and GitHub's official server in Docker.

```bash
switchboard add mcp filesystem -- npx -y @modelcontextprotocol/server-filesystem /tmp/opencode/real-demo
switchboard add mcp fetch -- uvx mcp-server-fetch
switchboard add mcp github --env 'GITHUB_PERSONAL_ACCESS_TOKEN={env:GITHUB_TOKEN}' -- \
  docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_TOOLSETS=repos,issues \
  ghcr.io/github/github-mcp-server
```

### 2a. Discovery, then inference

```bash
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/infer-demo.json
switchboard reset --yes
switchboard index                    # indexed 46 tools, 0 FEEDS edges
switchboard tools --server fetch     # fetch.fetch  consumes=[] produces=[]
```

Zero edges: MCP publishes no dependency types. Ask a model to name them:

```bash
export SWITCHBOARD_LLM_MODEL=opencode/glm-5.3-flash-free
switchboard annotate --infer         # one model call, ~40 s
# inferred capabilities for 46 tools, N of them produce data:
#   fetch.fetch                    produces=content
#   filesystem.read_file           produces=content
#   github.get_file_contents       produces=content

switchboard index                    # indexed 46 tools, ~23 FEEDS edges
switchboard graph | grep 'fetch.fetch -> filesystem'
# fetch.fetch -> filesystem.append_file  weight=1.0
# fetch.fetch -> filesystem.write_file  weight=1.0
```

The `fetch` server declares no output schema at all, so that edge could only come
from reading the description. N varies run to run (9-11 producers, 22-25 edges);
say the number you see.

### 2b. Declare four edges by hand, then run

The inferred graph is broad, so for the run show the precise diamond:

```bash
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/config.json
switchboard reset --yes
switchboard index                    # indexed 46 tools, 4 FEEDS edges
switchboard graph
# fetch.fetch -> filesystem.append_file  weight=1.0
# fetch.fetch -> filesystem.write_file  weight=1.0
# github.get_file_contents -> filesystem.append_file  weight=1.0
# github.get_file_contents -> filesystem.write_file  weight=1.0
```

Fetch a live page and write it to disk:

```bash
rm -f /tmp/opencode/real-demo/example.html /tmp/opencode/real-demo/log.txt
switchboard plan "fetch a web page and write it to a local file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html
switchboard run <id> \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html
cat /tmp/opencode/real-demo/example.html     # 208 bytes, live from example.com
```

Append instead of write, same source, different goal tool:

```bash
switchboard plan "fetch a web page and append it to a local log file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/log.txt
switchboard run <id> \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/log.txt
wc -c /tmp/opencode/real-demo/log.txt        # 208, appended
```

GitHub path - a different producer of the same `content` type:

```bash
switchboard plan "download README.md from a GitHub repository and write it to a local file" \
  --input owner=sakkshm --input repo=switchboard --input path=README.md
switchboard run <id> --input owner=sakkshm --input repo=switchboard --input path=README.md
wc -c /tmp/opencode/real-demo/README.md      # the live README size
```

`path=README.md` is relative to the filesystem server's root,
`/tmp/opencode/real-demo`. `fetch.fetch` and `github.get_file_contents` both
produce `content`; the planner picks the route whose inputs you supplied - a
`url` selects `fetch`, an `owner`/`repo` selects GitHub.

### 2c. Learning on real edges

Run the fetch plan three times, then read the weight back from HydraDB:

```bash
switchboard graph | grep fetch.fetch
# fetch.fetch -> filesystem.append_file  weight=1.0
# fetch.fetch -> filesystem.write_file  weight=1.6
```

1.0 -> 1.2 -> 1.4 -> 1.6, +0.2 per success, cap 2.0. The weight lives in the
store, not in code.

## 3. OpenCode TUI - the actual client

The point: the client configures one MCP server and sees two tools.

Fixture track:

```bash
export SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json
switchboard reset --yes && switchboard index
cp evals/harness/gateway/opencode.json opencode.json
opencode
```

Real track:

```bash
export GITHUB_TOKEN=$(gh auth token)
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/config.json   # already indexed in 2b
cp evals/harness/gateway/opencode.real.json opencode.json
opencode
```

Run `opencode` from the repo root: the config launches `.venv/bin/switchboard
serve` relative to it, and OpenCode reads `opencode.json` from the git root.

In the TUI, type the task in plain language:

```
comment on the failing test issue in acme/api, repo=acme/api, message=reproduced
```

What to watch for:

- the client reports exactly two tools: `switchboard_find`, `switchboard_run`
- the model calls `find` and gets a numbered multi-step plan
- it calls `run` with the `plan_id` and the same inputs
- the final result is the posted comment

For the real track use the same shape:

```
fetch a web page and write it to a local file, url=https://example.com, path=/tmp/opencode/real-demo/tui.html
```

The debug line `mcp connected server=switchboard tools=2` confirms the surface.
Remove `opencode.json` when done.

Headless equivalent, if you want a pasteable log:

```bash
OPENCODE_DISABLE_AUTOUPDATE=1 opencode --standalone run --auto --format json \
  -m opencode/deepseek-v4.1-flash-free \
  "Task: comment on the failing test issue in acme/api. Inputs: repo=acme/api, message=reproduced. Use the switchboard find tool, then switchboard run with the plan_id and the same inputs."
```

## 4. Many clients (daemon mode)

```bash
switchboard serve --http 127.0.0.1:8765
```

Point several MCP clients at `http://127.0.0.1:8765/mcp` instead of each
launching a stdio server. Verified with two concurrent clients on the same plan.

## 5. Numbers to show

Measured at the MCP `tools/list` layer: four servers direct is 24,832 chars /
5,453 tokens; the gateway is 747 chars / 169 tokens, 32.3x.

Eval, 24 tasks, 18 train / 6 holdout, 3 replicates, 5 rounds (`docs/RESULTS.md`):

| Mode | Holdout success | Order validity |
|---|---|---|
| BM25, top-4 relevance (naive) | 0.00 | 0.00 |
| BM25, goal + producer + dependency order (fair) | 0.08 | 0.96 |
| Uniform graph | 0.33 | 1.00 |
| Learned graph, round 1 | 1.00 | 1.00 |
| Learned graph, round 5 | 1.00 | 1.00 |

Say: the graph alone gets order right but takes the trap route 4 of 6 on the
holdout; learned weights fix the route in one training round.

## 6. If something fails

- `NoPlan` -> the graph is empty or the task has no path; run `switchboard index`.
- `switchboard: cannot reach <server>: ...` -> that upstream did not start; the
  message carries the upstream's own stderr line.
- `switchboard: cannot reach the graph store` -> `docker compose up -d --wait`.
- Wrong tool counts -> the two tracks share one graph; `switchboard reset --yes`
  before switching.
- `run` on an old `plan_id` reuses the stored steps. To route around a failed
  hop, call `plan` again.

## Do not say

- "Four tools in context." The client sees two; the plan names four.
- "The agent picks the right tool among sixty." The 60-tool baseline run was
  blocked by the client; the token number is measured at the MCP layer.
- "Production ready." No auth, no tenancy.
