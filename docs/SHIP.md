# Submission package draft

Prepared 2026-11-15 from the implemented product and observed checks. This is
reviewable copy and a recording outline, not a submitted form, uploaded video or
published presentation. Private repository visibility needs its owner's decision.

## Form copy

**Name:** Switchboard

**One-line description:** An MCP gateway that uses HydraDB dependency paths to
turn tasks into ordered tool chains while keeping the full catalog out of model context.

**Project description:** Switchboard gives an agent three tools: find, describe
and run. A local BM25 matcher selects candidates; HydraDB OSS returns dependency
paths; an independent type validator checks their order. The gateway binds
arguments from user input and earlier results, then executes actual MCP calls.
Runtime grants, expiring destructive confirmations, audit records and bounded
outputs provide visible control over execution.

The demo has 60 synthetic tools across issue triage, code review, incident response
and account workflows. Its issue chain represents 251 schema tokens versus 3,432
for the catalog, measured with cl100k_base over compact JSON. These are schema
payload counts, not total model usage. The left comparison column is static cost,
not a live baseline agent.

Successful plans persist in the graph and are hydrated for reuse. Fresh planning
uses one traversal; a warm hit uses zero new traversals. Grants, decisions and
persistence remain separately counted. Evals compare BM25 rank order, static
graph paths and learned edge weights on 24 tasks, three repetitions and eight
held-out paraphrases.

**How HydraDB is used:** OSS 0.4.0 stores tools, capability types, FEEDS edges,
grants, decisions and saved plans. MSpaths supplies ordered paths; Python scores
returned alternatives without a local traversal fallback. STEP edges persist
order and feedback updates FEEDS weights. MinIO provides S3-compatible storage.

**Track:** Agent context layer; confirm the exact event form label.

**Disclosure:** Tools/results are synthetic and writes append local JSONL. The
graph engine, planner, validator, binder, executor and MCP transport are real.
Identity is a demo stub. SelfGraph informed context and trust/evidence patterns;
it is not a runtime dependency.

| Form field | Required before submission |
|---|---|
| Team/contact | User-provided names and contact |
| Repository | Owner-approved access to sakkshm/switchboard |
| Live demo | Hosting selection and verified deployed URL |
| Video | Recording and upload URL |
| Slides | Final presentation artifact |
| Eval attachment | Reviewed final report, revision and dirty-tree disclosure |

## Three-minute recording script

| Time | Action | Narration |
|---|---|---|
| 0:00–0:25 | Show comparison counters | “The task needs four tools, but preloading schemas gives the agent sixty. Switchboard keeps the catalog in a gateway. These are measured schema payload costs.” |
| 0:25–0:55 | Run issue preset and inspect local write | “BM25 selects candidates, HydraDB returns paths, and a separate type check verifies dependencies. The gateway binds arguments and makes real MCP calls.” |
| 0:55–1:20 | Show path evidence and completed removal test | “The dependency graph is required. Without a path, planning refuses. There is no local ordering fallback.” |
| 1:20–1:45 | Run known plan as readonly | “A saved plan does not grant permission. Reads complete and the final write is refused and recorded.” |
| 1:45–2:05 | Repeat as demo persona | “Inputs are rebound. New planning traversals fall from one to zero; audit and permission calls still happen.” |
| 2:05–2:35 | Open final real evaluation report | “We compare the same synthetic tasks under ranked BM25, static graph and feedback. This report includes counts, repetitions, failures and held-out results.” Read actual final numbers only. |
| 2:35–3:00 | Architecture and scope | “The demo has a real OSS graph and MCP transport, with synthetic tools and local writes. Optional local filesystem and Git adapters are available. Next are broader integrations and production identity.” |

Do not empty the running demo graph during recording. Show the removal test from
a separate invocation. Narrate a learning gain only when the final report shows
it; report a flat curve plainly and emphasize measured context/order behavior.

## Five-slide outline

### 1. The problem

**Headline:** A task needs a few tools; the agent receives the whole catalog.

Show schema context, dependency order, rediscovered sequences and execution
boundaries. Introduce issue triage. Avoid unmeasured industry-wide failure rates.

### 2. The working system

**Headline:** Task → candidates → graph path → checked execution.

Show repo_search → list_issues → get_issue → add_comment with output/input bindings.
Use measured snapshot counts: 60 versus four schemas, 3,432 versus 251 tokens.
Label these schema payload measurements. Show fresh traversal one, warm reuse zero.

### 3. Measured evidence

**Headline:** Compare identical tasks under three planning variants.

Insert final report values for BM25/static/learning order validity, task success,
top-1 chain accuracy and p95. Until reviewed, label cells **pending measurement**.
State 24 tasks, 16 train, eight held-out paraphrases, three repetitions and five
training rounds. Include graph removal and executor denied-write evidence.

### 4. Intended users and adoption

**Headline:** A gateway for developers connecting agents to growing MCP catalogs.

Target hypothesis: teams needing repeatable multi-tool workflows, permissions and
traces. Adoption path: configure a server, review capability/binding metadata,
expose the three-tool gateway. Broader integrations, pricing and customer validation
remain future work; do not imply customers or paid traction.

### 5. Scope and next work

**Headline:** Prove the chain, then expand integrations.

Distinguish real graph/planner/executor/grants/audit from synthetic catalog and
traffic. Optional read-only filesystem/Git adapters have a separate setup. Next: trusted identity, deployment and broader
evals. Link only final verified repository, demo and video URLs.

## Final publication checks

- Verify report revision and retain dirty-worktree disclosure.
- Replace pending scores with actual values, including flat or negative curves.
- Verify the deployed demo and intended access settings.
- Obtain the owner's repository visibility/access decision.
- Record and upload the final video and slides.
- Submit actual URLs and record the form confirmation.
