# Results

Measured 2026-11-15 on HydraDB OSS 0.4.0 with MinIO, four synthetic MCP servers,
and OpenCode v2.2.5. Raw data: `evals/results/latest.json` (learning curve) and
`evals/results/tokens.json` (tool-schema context).

## Headline

| Metric | Value |
|---|---|
| Upstream tools | 60 across 4 MCP servers |
| Gateway tools the client configures | 2 (`find`, `run`) |
| Tool-schema context, native | 5,453 tokens |
| Tool-schema context, gateway | 169 tokens |
| Context reduction | 32.3x |
| Holdout task success, BM25 baseline | 0.00 |
| Holdout task success, uniform graph | 0.33 |
| Holdout task success, learned graph | 1.00 after one training round |
| Order validity, every graph plan | 1.00 |
| Replicates | 3, zero spread |

The graph supplies order. The learned weights pick which valid route to take.
Neither alone is enough.

## Setup

Four fixture MCP servers (`repo`, `issue`, `incident`, `account`), 15 tools each.
`switchboard index` discovers them from `tools/list` and writes:

- 60 `:Tool` nodes with declared `consumes` / `produces` types
- 69 `:FEEDS` edges where one tool produces a type another consumes

Each `find` call persists its plan as `:Plan` / `:STEP` nodes with a random
48-bit id, so the plan count grows with use and is not an index output.

The eval uses 24 tasks in four families: 18 train, 6 holdout, three replicates,
five training rounds. Each family also contains a plausible sibling that always
fails at execution:

| Real tool | Trap sibling | Same types? |
|---|---|---|
| `repo.repo_search` | `repo.guess_repo` | yes |
| `repo.locate_file` | `repo.locate_file_fast` | yes |
| `incident.search_logs` | `incident.search_logs_legacy` | yes |
| `account.find_account` | `account.find_account_legacy` | yes |

Both routes pass the consumes/produces check. Only execution and the learned
edge weights tell them apart. That is the point of the fixture.

## Tool-schema context

Method: read the real MCP `tools/list` objects from the four servers and from
`switchboard serve`, serialize the fields a client receives (`name`,
`description`, `inputSchema`, `outputSchema` when present) as canonical JSON,
and count with `cl100k_base`. Same serializer both sides.

| Side | Tools | Characters | Tokens |
|---|---|---|---|
| Native, four servers | 60 | 24,832 | 5,453 |
| Gateway | 2 | 747 | 169 |

This is the schema payload a direct-tool client carries, measured at the MCP
protocol layer. It is not a measurement of one client's assembled prompt.

## Learning curve

Holdout, 6 tasks, median of 3 replicates. Values are deterministic across
replicates (spread min = max).

| Mode | Round | Top-1 | Order | Success | Precision | Recall | p95 |
|---|---|---|---|---|---|---|---|
| BM25, no graph | - | 0.00 | 0.00 | 0.00 | 0.38 | 0.38 | 12 ms |
| Uniform graph | 0 | 0.33 | 1.00 | 0.33 | 0.83 | 0.83 | 3,423 ms |
| Learned graph | 0 | 0.33 | 1.00 | 0.33 | 0.83 | 0.83 | 2,943 ms |
| Learned graph | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 3,149 ms |
| Learned graph | 2 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 3,098 ms |
| Learned graph | 3 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 3,344 ms |
| Learned graph | 4 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 3,125 ms |
| Learned graph | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 3,377 ms |

```
top-1 success, holdout
1.00 |       *     *     *     *     *
0.33 | *     .     .     .     .     .   <- learned r0, uniform r0-r5
0.00 | x                                 <- BM25
     +-----+-----+-----+-----+-----+-----+
        r0    r1    r2    r3    r4    r5
```

Train split, for reference: uniform graph 0.44 success, 0.86 precision/recall.
The learned graph is trained on that split and scored on the holdout.

## Why each mode behaves this way

**BM25 (0.00).** BM25 ranks individual tools by task similarity. It has no notion
of dependencies, so its top four tools are not a chain. The first step does not
receive the type it consumes, and the run fails. Order validity is 0.00 because
the plan is not ordered by data flow at all. This is the baseline the graph must
beat, not a strawman: it is the retrieval most gateways use today.

**Uniform graph (0.33).** Traversal over `FEEDS` produces a topologically valid
four-step chain every time (order 1.00). With all weights equal, the planner has
no reason to prefer `repo_search` over `guess_repo`; both consume `repo` and
produce `repo_meta`. Four of six holdout tasks take a trap route and fail at
execution. The graph alone fixes order, not route choice.

**Learned graph (1.00 after one round).** A failed step lowers the weights on
the edges it used by 0.2; a successful chain raises them by 0.2. Weights live in
HydraDB as `(:Tool)-[:FEEDS {weight}]->(:Tool)`, not in code or in the prompt.
After the first training round the trap edges are below the real routes, and all
six holdout tasks succeed. Rounds 2-5 stay at 1.00, so the result is stable, not
a lucky single update.

Latency: graph plans cost roughly 3 s p95 end to end (plan plus four upstream
calls) because each `MSpaths` call takes about 0.8 s in OSS. BM25 is 12 ms but
produces an unusable plan. A production gateway would cache graph results.

## OpenCode A/B

### Proven: gateway path

OpenCode v2.2.5, `codemode: false`, one MCP server (`switchboard`), 2 tools
visible. Task: "comment on the failing test issue in acme/api", inputs
`repo=acme/api`, `message=reproduced`.

1. The model called `switchboard_find`.
2. `find` returned plan `227123842785482`, goal `issue.add_comment`, four steps:
   `repo.repo_search`, `repo.list_issues`, `issue.get_issue`, `issue.add_comment`.
3. The model called `switchboard_run` with the plan id and the same inputs.
4. `run` returned `ok: true` and four results, ending in
   `comment on acme/api#42: failing test in checkout: reproduced`.

The HTTP daemon was also verified with two concurrent MCP clients on the same
plan shape: both listed exactly `find` and `run`, both got a four-step plan, and
both runs returned `ok: true`.

### Not proven: live 60-direct-tool baseline

The four fixture servers connect to OpenCode (15 tools each, 60 total), and the
project config sets `codemode: false` on each, but OpenCode v2.2.5 did not put
those tools in the model's catalog. A control server with two trivial tools
stayed invisible the same way. The gateway's tools were callable in the same
client and the same worktree.

Tried and failed: one server only, global servers disabled, custom probe servers
with plain / modeled / dict outputs, a renamed server, `environment`, camelCase
`codeMode`, an isolated `XDG_CONFIG_HOME`, and a detached worktree so the config
sat at the git root. `opencode mcp list` is not evidence; it reports "No MCP
servers configured" even when the debug log says `mcp connected`.

Consequence: the 60-vs-2 token number is measured at the MCP protocol layer,
which is where the context cost actually lives, and the live OpenCode session
proves the gateway path only. The learning curve measures the planner, not a
model choosing among 60 tools. Do not present it as the latter.

## Reproduce

```bash
cp .env.example .env
docker compose up -d --wait

python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

export SWITCHBOARD_CONFIG=$PWD/evals/harness/switchboard.json
switchboard index
switchboard index                  # same tools and edges: 60 and 69

python evals/chain_check.py        # six four-step chains
python evals/run.py --reps 3 --rounds 5
python evals/baseline_tokens.py
pytest -q
ruff check .
```

OpenCode gateway check: copy `evals/harness/gateway/opencode.json` to the repo
root as `opencode.json`, then run from the repo root:

```bash
opencode run --format json \
  "Task: comment on the failing test issue in acme/api. Inputs: repo=acme/api, message=reproduced. Use switchboard find, then switchboard run with the plan id."
```

## Limitations

- The catalog is synthetic: four fixture servers, 24 tasks, 6 holdout. It proves
  the mechanism, not behavior on a production registry.
- Three replicates, deterministic by construction. There is no variance to
  report, which is useful for verification and not a substitute for a larger
  sample.
- The `consumes` / `produces` convention is ours. Real MCP servers often publish
  no output types; indexing them needs a fallback or a capability annotation.
- OpenCode baseline caveat above.
- The gateway is a single-user local process. No auth, tenancy, or quotas.
