# Loom script (lean)

Target 2:45 to 3:00, one take, terminal only. Story: one gateway, any MCP client,
and the dependency graph lives in HydraDB. Everything runs against the **default
config** (`~/.config/switchboard/config.json`), so there is no `SWITCHBOARD_CONFIG`
to explain.

## Off camera, once

```bash
docker compose up -d --wait                     # HydraDB OSS + MinIO, healthy
docker pull ghcr.io/github/github-mcp-server:latest
export GITHUB_TOKEN=$(gh auth token)            # the GitHub MCP server reads this
export SWITCHBOARD_LLM_MODEL=opencode/glm-5.3-flash-free   # annotate --infer
```

- Big font, dark theme, run from the repo root, empty scrollback, no secrets shown.
- npx (filesystem) and uvx (fetch) packages are cached; the Docker image is pulled.
- `annotate --infer` is one model call, about 35 s. Say the line before it and trim
  the wait in the edit.
- Optional: open `site/index.html` in a tab for a 5 s intro shot. Keep it short.

## 0:00 Clone and install (25 s)

Say: "Switchboard is a universal MCP gateway. The agent gets two tools, find and
run, instead of the whole upstream catalog. The dependencies between tools live
in HydraDB as a graph."

```bash
git clone https://github.com/sakkshm/switchboard
cd switchboard
./install.sh
switchboard status
```

Expected:

```
==> Checking Python
    Python 3.12.3
==> Creating the virtualenv in .venv
    created
==> Installing Switchboard (editable) and dev dependencies
    installed switchboard 0.1.0
==> Linking the switchboard command into /home/you/.local/bin
    /home/you/.local/bin/switchboard -> /path/to/switchboard/.venv/bin/switchboard
    command works
Done. Next: switchboard status
HydraDB http://127.0.0.1:18443  ready=True
servers=0 tools=0 edges=0 plans=0
```

Say: "install.sh builds the venv, installs the CLI, and puts `switchboard` on
PATH. HydraDB is up and the graph is empty."

## 0:25 Add real MCP servers (25 s)

Say: "Three real MCP servers. No mocks."

```bash
switchboard add mcp filesystem -- npx -y @modelcontextprotocol/server-filesystem /tmp/opencode/demo
switchboard add mcp fetch -- uvx mcp-server-fetch
switchboard add mcp github --env 'GITHUB_PERSONAL_ACCESS_TOKEN={env:GITHUB_TOKEN}' -- \
  docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_TOOLSETS=repos,issues \
  ghcr.io/github/github-mcp-server
```

Expected: `added filesystem: 16 tools`, `added fetch: 1 tools`,
`added github: 29 tools`.

Say: "Each add connects, lists the tools, and stores only the connection. 46
tools. None of the catalog is hardcoded."

## 0:50 Index, then infer the edges (55 s)

Say: "Index discovers the tools. MCP tells us what a tool needs, but never what it
returns."

```bash
switchboard index
```

Expected:

```
indexed 46 tools, 0 FEEDS edges
no tool declares consumes/produces; run switchboard annotate --infer
```

Say: "Zero edges. So I ask a model to read the descriptions and name what each
tool produces."

```bash
switchboard annotate --infer
```

Expected (about 35 s):

```
inferring capabilities for 46 tools, one model call...
inferred capabilities for 46 tools, 10 of them produce data:
  fetch.fetch                    produces=content
  filesystem.read_file           produces=content
  github.get_file_contents       produces=content
  ...
```

Say: "The fetch server declares no output schema at all, yet the model works out
that it returns content. Now index again and the edges appear."

```bash
switchboard index
switchboard list
switchboard graph
```

Expected:

```
indexed 46 tools, 22 FEEDS edges
filesystem   stdio   16 tools  npx -y @modelcontextprotocol/server-filesystem /tmp/opencode/demo
fetch        stdio    1 tools  uvx mcp-server-fetch
github       stdio   29 tools  docker run -i --rm ... ghcr.io/github/github-mcp-server
fetch.fetch -> filesystem.append_file  weight=1.0
fetch.fetch -> filesystem.write_file  weight=1.0
...
22 edges
```

Say: "That edge, fetch to write_file, is the plan. It lives in HydraDB, not in
code."

## 1:45 Sample plan and run (30 s)

```bash
switchboard plan "fetch a web page and write it to a local file" \
  --input url=https://example.com --input path=example.html
switchboard run <plan_id> \
  --input url=https://example.com --input path=example.html
cat /tmp/opencode/demo/example.html
```

Expected:

```
plan <id>  goal filesystem.write_file
  task: fetch a web page and write it to a local file
  0. fetch.fetch                url <- input
  1. filesystem.write_file      path <- input, content <- fetch.fetch
inputs: url, path
run: switchboard run <id> --input url=https://example.com --input path=example.html

  ok   0. fetch.fetch                Contents of https://example.com/: # Example Domain ...
  ok   1. filesystem.write_file      {"content": "Successfully wrote to example.html"}
done: 2/2 steps ok
```

Say: "Two steps, ordered by the graph. The page was really fetched and really
written - 208 bytes on disk."

## 2:15 OpenCode, the client (35 s)

Say: "Any MCP client. Here is OpenCode, configured with one server."

```bash
cp evals/harness/gateway/opencode.default.json opencode.json
cat opencode.json
opencode
```

In the TUI, type:

```
fetch a web page and write it to a local file, url=https://example.com, path=tui.html
```

Say: "OpenCode sees exactly two tools: switchboard_find and switchboard_run. It
calls find, gets the plan, then calls run. It never sees the other 44 tools."

Show: the `find` plan, the `run` result, and the debug line
`mcp connected server=switchboard tools=2`.

## 2:50 Learning, and close (10 s)

```bash
switchboard graph | grep fetch.fetch
# fetch.fetch -> filesystem.write_file  weight=1.4   (was 1.0)
```

Say: "Every successful run raises the edge weight in HydraDB. The same loop
learns on real edges. Repo: github.com/sakkshm/switchboard."

## After recording

```bash
rm -f opencode.json        # it is git-ignored, remove it so the next run is clean
```

## Do not say

- "The client sees four tools." It sees two; the plan names the steps.
- "The agent picks the right tool among sixty." The 60-tool model baseline was
  blocked by the client; the token number is measured at the MCP layer.
- "Production ready." No auth, no tenancy.
- Do not read the inferred producer list aloud. It varies run to run (9-11
  producers, 22-25 edges); say the number you see.
