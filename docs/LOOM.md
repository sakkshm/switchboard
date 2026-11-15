# Loom script

Target: 2:30 to 3:00. One take, terminal plus the static page. No live editing.

## Before recording

```bash
docker compose ps                       # hydradb and minio healthy
export SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json
switchboard status                      # servers=4 tools=60 edges=69
```

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

## 0:40 - Index (35 s)

Say: "Indexing is discovery, not a hand-written catalog."

```bash
switchboard index
switchboard status
```

Expected:

```
indexed 60 tools, 69 FEEDS edges
servers=4 tools=60 edges=69
```

Say: "Sixty tools and sixty-nine edges where one tool's output feeds another's
input. The fixture has six known four-step chains. Running it again does not
duplicate anything."

## 1:15 - Plan (30 s)

```bash
switchboard plan "comment on the failing test issue in acme/api" \
  --input repo=acme/api --input message=reproduced
```

Say: "One graph traversal returns the order. repo_search, list_issues,
get_issue, add_comment. Every step consumes a type produced earlier. The planner
never sorts tools by hand."

Highlight the four steps and the plan id in the output.

## 1:45 - OpenCode (40 s)

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

## 2:25 - Results (25 s)

Show: `docs/RESULTS.md` curve table.

Say: "The graph alone gets order right but picks a trap route four times out of
six. With learned weights, the holdout goes from 0.33 to 1.00 after one training
round and stays there. Three replicates, zero spread. The graph fixes order; the
weights fix the route."

Mention once: "The 60-vs-2 token number is measured from the real MCP tool
schemas. OpenCode v2.2.5 would not surface the raw fixture tools for a live
60-tool baseline; that caveat is in the results."

## 2:50 - Close (10 s)

Say: "HydraDB OSS and MinIO, one gateway, any MCP client. Repo is
github.com/sakkshm/switchboard, branch agent slash gateway. Thanks."

Show: the repository URL and the footer.

## Do not say

- "Four tools in context." The client sees two; the plan names four.
- "The agent picks the right tool among sixty." The baseline model run was
  blocked by the client.
- "Production ready." There is no auth or tenancy.
