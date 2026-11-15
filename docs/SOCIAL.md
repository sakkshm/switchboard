# Submission posts

Tag HydraDB as `@hydra_db` on X and `HydraDB` on LinkedIn
(linkedin.com/company/hydradb). Attach the learning-curve screenshot and, if
the platform allows, the Loom link.

## X

Your agent does not need 60 tools. It needs 4, in the right order.

Switchboard is an MCP gateway that stores tool dependencies in @hydra_db,
returns an ordered plan, and learns edge weights from execution.

60 tools -> 2. 5,453 -> 169 schema tokens. 32.3x.
Holdout success 0.33 -> 1.00 after one training round.

Open source, HydraDB OSS + MinIO:
github.com/sakkshm/switchboard

## LinkedIn

Most agent tool failures are not model failures. They are ordering failures.

Tool B needs an ID that tool A produces. The model sees both schemas, picks by
similarity, and finds out by failing. With 60 tools that is 5,453 tokens of
schema on every turn, to use maybe four.

I built Switchboard for the HydraDB hackathon. It is a universal MCP gateway:

- `switchboard index` discovers every upstream tool and writes the dependency
  graph into HydraDB OSS.
- The client configures one server and sees two tools: `find` and `run`.
- `find` runs one graph traversal and returns an ordered four-step plan, checked
  against consumed and produced types.
- `run` executes the chain and raises or lowers HydraDB edge weights from the
  result.

Measured on 24 tasks, 18 train and 6 holdout, 3 replicates:

- Tool-schema context: 5,453 -> 169 tokens, 32.3x less.
- Order validity: 1.00 for every graph plan.
- Holdout success: BM25 0.00, uniform graph 0.33, learned graph 1.00 after one
  training round and stable through round five.

The interesting part is why the graph alone is not enough. Every fixture family
has a trap tool with identical input and output types that fails at execution.
The graph gets the order right and still takes the trap route four times out of
six. The learned weights are what pick the working route. Order comes from
HydraDB's traversal; route choice comes from execution data stored back in the
graph.

Tested as a real MCP client in OpenCode, over stdio and over an HTTP daemon with
two concurrent clients. The fixtures and eval harness are in the repo so the
numbers can be reproduced.

Repo: github.com/sakkshm/switchboard
Demo: [Loom link]

Built with HydraDB OSS 0.4.0 and MinIO. The catalog is synthetic; the graph,
plans, weights, and MCP transports are real.

## Notes

- Do not claim a live 60-native-tool model baseline. OpenCode v2.2.5 did not
  expose the raw fixture tools to the model; the token number is measured from
  the MCP `tools/list` payloads. Say that if asked.
- Do not claim four tools in context. The client sees two; the plan names four.
- Put the Loom link in both posts before publishing.
