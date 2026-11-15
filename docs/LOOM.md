# Loom script

Target: 3:00 to 3:30. One take, terminal plus the static page. No live editing.

## Before recording

```bash
docker compose ps                       # hydradb and minio healthy
docker pull ghcr.io/github/github-mcp-server:latest
export GITHUB_TOKEN=$(gh auth token)
export SWITCHBOARD_LLM_MODEL=opencode/glm-5.3-flash-free
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/infer-demo.json
switchboard status                      # servers=3
```

- The three real servers are already added (`docs/TESTING.md` has the `add mcp`
  lines). `switchboard reset --yes` then `switchboard index` on camera clears the
  fixture graph and rebuilds the real one.
- Two configs, same three servers. `/tmp/opencode/real-demo/infer-demo.json` has
  no annotations (the `--infer` beat). `/tmp/opencode/real-demo/config.json` has
  the four edges declared by hand (the run beat).
- Delete `/tmp/opencode/real-demo/example.html` and `README.md` first so the
  writes are visibly new.
- `switchboard annotate --infer` makes one model call and takes about 40 s; the
  Loom line before it sets the expectation, and the wait is trimmed in the edit.

- Open `site/index.html` in a browser tab, scrolled to the hero.
- Open `docs/RESULTS.md` in a second tab.
- Terminal: large font, dark theme, repo root, no secrets in scrollback.
- If weights were changed by demo runs, the curve in `evals/results/latest.json`
  is still the source of truth. Do not re-run the eval on camera.

## 0:00 - Problem (20 s)

Say: "An agent with sixty MCP tools pays for sixty tool schemas on every turn.
Here that is 5,453 tokens. Worse, it still has to guess the order: tool B needs
an ID that tool A produces. It finds out by failing."

Show: the hero on `site/index.html`, then the "60 to 2" strip.

## 0:20 - What Switchboard is (20 s)

Say: "Switchboard is a universal MCP gateway. It discovers every upstream tool,
writes the dependencies into HydraDB as a graph, and gives the agent two tools:
find and run. The client never sees the upstream catalog."

Show: the architecture cards on the page, then the graph schema block.

## 0:40 - Index real servers, infer the edges (55 s)

Say: "Indexing is discovery, not a hand-written catalog. These are three real MCP
servers: the official filesystem server, the fetch server, and GitHub's own
server in Docker."

```bash
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/infer-demo.json
switchboard reset --yes
switchboard index
switchboard tools --server fetch
```

Expected:

```
indexed 46 tools, 0 FEEDS edges
fetch.fetch  consumes=[] produces=[]
```

Say: "Forty-six real tools, zero edges. MCP does not publish dependency types,
and the schemas are not enough: the fetch server declares no output at all, and
the filesystem server claims every one of its tools returns content."

```bash
switchboard annotate --infer
```

Say: "So I ask a model. It keeps the required inputs as consumes, then reads each
description and names what the tool actually returns. It works out that fetch
returns content - the one edge no schema could reveal."

Expected:

```
inferring capabilities for 46 tools, one model call...
inferred capabilities for 46 tools, 11 of them produce data:
  fetch.fetch                    produces=content
  filesystem.read_file           produces=content
  github.get_file_contents       produces=content
  ...
```

```bash
switchboard index
switchboard graph | grep 'fetch.fetch -> filesystem'
```

Expected:

```
indexed 46 tools, 23 FEEDS edges
fetch.fetch -> filesystem.append_file  weight=1.0
fetch.fetch -> filesystem.write_file  weight=1.0
```

Say: "The load-bearing edge is back, discovered, not declared. A hand-written
capability header still wins, then a manual annotate, then this. The graph is
what makes a plan possible at all."

## 1:20 - Run real tools (45 s)

Say: "The inferred graph is deliberately broad, so a task can route to a loose
goal. For the run I declare the four edges by hand. Header beats manual beats
inferred."

```bash
export SWITCHBOARD_CONFIG=/tmp/opencode/real-demo/config.json
switchboard reset --yes
switchboard index
switchboard graph
```

Expected:

```
indexed 46 tools, 4 FEEDS edges
fetch.fetch -> filesystem.append_file  weight=1.0
fetch.fetch -> filesystem.write_file  weight=1.0
github.get_file_contents -> filesystem.append_file  weight=1.0
github.get_file_contents -> filesystem.write_file  weight=1.0
```

Say: "Now the real loop. No mock server anywhere."

```bash
switchboard plan "fetch a web page and write it to a local file" \
  --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html
switchboard run <plan_id> --input url=https://example.com --input path=/tmp/opencode/real-demo/example.html
cat /tmp/opencode/real-demo/example.html
```

Say: "The graph returned fetch then write_file. The page was really fetched and
really written." Repeat the plan and run twice more, then:

```bash
switchboard graph | grep fetch.fetch
# fetch.fetch -> filesystem.write_file  weight=1.6
```

Say: "Every successful run raises the edge weight in HydraDB. That is the
continual learning, on real edges."

```bash
switchboard plan "download README.md from a GitHub repository and write it to a local file" \
  --input owner=sakkshm --input repo=switchboard --input path=README.md
switchboard run <plan_id> --input owner=sakkshm --input repo=switchboard --input path=README.md
wc -c /tmp/opencode/real-demo/README.md      # 8692, live from GitHub
```

Say: "The heavy fetch edge did not hijack the GitHub task. The planner takes the
reachable goal whose first tool ranks best for the task."

## 1:55 - Multi-step plan (25 s)

```bash
export SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json
switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced
```

Say: "On a four-step chain the traversal returns the whole order. repo_search,
list_issues, get_issue, add_comment. Every step consumes a type produced
earlier. The planner never sorts tools by hand."

## 2:20 - OpenCode (35 s)

Show the gateway config (`evals/harness/gateway/opencode.json`): one server, two
tools, `codemode: false`.

```bash
cp evals/harness/gateway/opencode.json opencode.json
opencode --standalone run --auto --format json -m opencode/deepseek-v4.1-flash-free \
  "Task: comment on the failing test issue in acme/api. Inputs: repo=acme/api, message=reproduced. Use the switchboard find tool, then switchboard run with the plan_id and the same inputs."
```

Say: "The model calls find, gets a four-step plan, then calls run. The gateway
executes all four upstream calls. The final result is the posted comment."

Show: `switchboard_find` and `switchboard_run` lines, then the final result
`comment on acme/api#42: failing test in checkout: reproduced`.

## 2:55 - Results (20 s)

Show: `docs/RESULTS.md` curve table.

Say: "The graph alone gets order right but picks a trap route four times out of
six. With learned weights, the holdout goes from 0.33 to 1.00 after one training
round and stays there. Three replicates, zero spread. The graph fixes order; the
weights fix the route."

Mention once: "The 60-vs-2 token number is measured from the real MCP tool
schemas. OpenCode v2.2.5 would not surface the raw fixture tools for a live
60-tool baseline; that caveat is in the results."

## 3:15 - Close (10 s)

Say: "HydraDB OSS and MinIO, one gateway, any MCP client. Repo is
github.com/sakkshm/switchboard. Thanks."

Show: the repository URL and the footer.

## Do not say

- "Four tools in context." The client sees two; the plan names four.
- "The agent picks the right tool among sixty." The baseline model run was
  blocked by the client.
- "Production ready." There is no auth or tenancy.
