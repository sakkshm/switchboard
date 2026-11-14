"""Rebuild the committed synthetic catalog and the golden task set."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tool(key, description, family, stage, consumes, produces, args, *, outputs=None, risk="read", distractor=False):
    properties = {name: {"type": "integer" if name == "number" else "string"} for name in args}
    description += f" [consumes: {','.join(consumes)} | produces: {','.join(produces)}]"
    return {"key": key, "name": key, "server": "fixture", "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": list(args), "additionalProperties": False},
            "consumes": consumes, "produces": produces, "risk": risk, "writes": risk != "read",
            "args": args, "outputs": outputs or {}, "version": "1", "entry": not consumes,
            "family": family, "stage": stage, "distractor": distractor,
            "annotations": {"readOnlyHint": risk == "read", "destructiveHint": risk == "destructive", "openWorldHint": False}}


def build_catalog():
    records = []
    definitions = [
        ("issue", [
            ("repo_search", "Find a repository for issue triage and failing tests", [], ["repo"], {"query": "$input.repo"}, {"repo": "full_name"}),
            ("list_issues", "List open failing test issues in a repository", ["repo"], ["issue_number"], {"repo": "$steps.0.result.full_name"}, {"issue_number": "0.number"}),
            ("get_issue", "Read a failing test issue by repository and issue number", ["repo", "issue_number"], ["issue"], {"repo": "$steps.0.result.full_name", "number": "$steps.1.result.0.number"}, {"issue": "number"}),
            ("add_comment", "Comment on the failing test issue; add a comment or reply to an issue in a repository", ["repo", "issue"], ["comment"], {"repo": "$steps.0.result.full_name", "number": "$steps.2.result.number", "body": "$input.message"}, {"comment": "id"}),
        ], ["list_archived_issues", "list_cached_issues"], ["search_releases", "read_release", "list_milestones", "read_milestone", "list_labels", "read_label", "read_repository_stats", "read_contributors", "delete_repository"]),
        ("review", [
            ("locate_file", "Locate a source file for code review and authentication changes", [], ["file_path"], {"query": "$input.path"}, {"file_path": "path"}),
            ("read_file", "Read source file content for a code review", ["file_path"], ["file_content"], {"path": "$steps.0.result.path"}, {"file_content": "content"}),
            ("get_diff", "Get the code change diff from source content and file path", ["file_path", "file_content"], ["diff"], {"path": "$steps.0.result.path", "content": "$steps.1.result.content"}, {"diff": "diff"}),
            ("post_review", "Post a code review for the authentication diff; submit review feedback on a code change", ["diff"], ["review"], {"diff": "$steps.2.result.diff", "body": "$input.message"}, {"review": "id"}),
        ], ["read_cached_file", "read_old_file"], ["list_branches", "read_branch", "list_tags", "read_tag", "list_commits", "read_commit", "read_build_status", "read_language_stats", "delete_branch"]),
        ("incident", [
            ("search_logs", "Search error logs for a service incident", [], ["trace_id"], {"service": "$input.service"}, {"trace_id": "trace_id"}),
            ("get_trace", "Read the trace of a service error and summarize the incident", ["trace_id"], ["trace"], {"trace_id": "$steps.0.result.trace_id"}, {"trace": "summary"}),
            ("open_ticket", "Prepare an incident ticket draft from the error trace; no external write", ["trace"], ["ticket"], {"summary": "$steps.1.result.summary"}, {"ticket": "ticket_id"}),
            ("notify_channel", "Notify the incident channel about the checkout error; send an incident notification to the channel", ["ticket"], ["notification"], {"ticket_id": "$steps.2.result.ticket_id", "channel": "$input.channel", "body": "$input.message"}, {"notification": "id"}),
        ], ["get_cached_trace", "get_archived_trace"], ["list_services", "read_service", "list_deployments", "read_deployment", "list_alert_rules", "read_alert_rule", "read_uptime", "read_error_budget", "delete_logs"]),
        ("account", [
            ("find_account", "Find a customer account for order support", [], ["account_id"], {"query": "$input.account"}, {"account_id": "account_id"}),
            ("list_orders", "List the latest orders belonging to a customer account", ["account_id"], ["order_id"], {"account_id": "$steps.0.result.account_id"}, {"order_id": "0.order_id"}),
            ("get_order", "Read details and amount of a customer order", ["order_id"], ["order"], {"order_id": "$steps.1.result.0.order_id"}, {"order": "order_id"}),
            ("issue_refund", "Issue a refund for the latest account order; refund a duplicate customer order", ["order"], ["refund"], {"order_id": "$steps.2.result.order_id", "reason": "$input.reason"}, {"refund": "id"}),
        ], ["list_archived_orders", "list_cached_orders"], ["list_products", "read_product", "list_invoices", "read_invoice", "list_subscriptions", "read_subscription", "read_account_settings", "read_payment_methods", "delete_account"]),
    ]
    for family, chain, alternatives, extras in definitions:
        for stage, (key, description, consumes, produces, args, outputs) in enumerate(chain):
            risk = ("destructive" if family == "account" else "write") if stage == 3 else "read"
            records.append(tool(key, description, family, stage, consumes, produces, args, outputs=outputs, risk=risk))
        for name in alternatives:
            _, description, consumes, produces, args, outputs = chain[1]
            records.append(tool(name, description + " from an archived stale snapshot", family, 1, consumes, produces, args, outputs=outputs, distractor=True))
        for index, name in enumerate(extras):
            # Unrelated tools are genuinely callable and have their own types.
            consumes = [] if index % 2 == 0 else [family + "_extra_" + str(index - 1)]
            risk = "destructive" if name.startswith("delete_") else "read"
            records.append(tool(name, name.replace("_", " ").capitalize() + " in the synthetic " + family + " service",
                                family, 4, consumes, [family + "_extra_" + str(index)], {"query": "$input.query"}, risk=risk))
    assert len(records) == 60
    return records


def build_tasks():
    families = [
        ("issue", ["repo_search", "list_issues", "get_issue", "add_comment"],
         ["comment on the failing test issue in acme/api", "add a comment to the failing test issue", "reply with a comment on the open test issue", "comment on the repository issue about failing tests", "add a comment to the open repository issue", "comment on an issue in the acme/api repository"],
         {"repo": "acme/api", "message": "Investigating the failing test."}),
        ("review", ["locate_file", "read_file", "get_diff", "post_review"],
         ["post a code review for the authentication diff", "submit review feedback for the source code diff", "post a review of the changed code", "post code review feedback on authentication", "submit a code review for the file changes", "review the authentication code diff and post feedback"],
         {"path": "src/auth.py", "message": "Reviewed the authentication change."}),
        ("incident", ["search_logs", "get_trace", "open_ticket", "notify_channel"],
         ["notify the incident channel about the checkout error", "send an incident notification to the channel", "notify the channel about service errors", "send the incident ticket to the notification channel", "notify the channel of the checkout service incident", "notify the incident channel about the error trace"],
         {"service": "checkout", "channel": "incidents", "message": "Investigating checkout errors."}),
        ("account", ["find_account", "list_orders", "get_order", "issue_refund"],
         ["issue a refund for the latest account order", "refund the duplicate customer order", "issue a refund on the latest customer order", "refund an order for the customer account", "issue an account order refund", "refund the latest duplicate order"],
         {"account": "customer@example.test", "reason": "Duplicate order"}),
    ]
    return [{"id": f"{family}-{i + 1}", "family": family, "split": "train" if i < 4 else "holdout",
             "task": task, "expected_chain": chain, "expected_values": values, "allowed_extras": [],
             "expected_end_state": {"ok": True}, "confirmation_token": "fixture-eval-confirmation" if family == "account" else None}
            for family, chain, tasks, values in families for i, task in enumerate(tasks)]


def main():
    (ROOT / "catalog/catalog.json").write_text(json.dumps(build_catalog(), indent=2) + "\n", encoding="utf-8")
    destination = ROOT / "evals/tasks.jsonl"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text("".join(json.dumps(task) + "\n" for task in build_tasks()), encoding="utf-8")


if __name__ == "__main__":
    main()
