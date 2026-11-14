"""Issue tools: comment, close, and the usual issue operations."""

from mcp.server.mcpserver import MCPServer

from .common import check

mcp = MCPServer("issue", instructions="Issue tracking tools.")


@mcp.tool(description="Get an issue by number. [consumes: issue_number | produces: issue]")
def get_issue(issue_number: int) -> str:
    check("get_issue")
    return f"acme/api#{issue_number}: failing test in checkout"


@mcp.tool(description="Comment on an issue to report a finding. [consumes: issue, message | produces: comment]")
def add_comment(issue: str, message: str) -> str:
    check("add_comment")
    return f"comment on {issue}: {message}"


@mcp.tool(description="Close an issue as stale. [consumes: issue | produces: closed_issue]")
def close_issue(issue: str) -> str:
    check("close_issue")
    return f"closed {issue}"


@mcp.tool(description="Read the comment thread on an issue. [consumes: issue | produces: comments]")
def list_comments(issue: str) -> str:
    check("list_comments")
    return f"3 comments on {issue}"


@mcp.tool(description="Create an issue from a title. [consumes: issue_title | produces: issue]")
def create_issue(issue_title: str) -> str:
    check("create_issue")
    return f"acme/api#99: {issue_title}"


@mcp.tool(description="Assign an issue to an owner. [consumes: issue | produces: assigned_issue]")
def assign_issue(issue: str) -> str:
    check("assign_issue")
    return f"assigned {issue}"


@mcp.tool(description="Add labels to an issue. [consumes: issue | produces: labeled_issue]")
def label_issue(issue: str) -> str:
    check("label_issue")
    return f"labeled {issue}"


@mcp.tool(description="Reopen a closed issue. [consumes: issue | produces: open_issue]")
def reopen_issue(issue: str) -> str:
    check("reopen_issue")
    return f"reopened {issue}"


@mcp.tool(description="List labels in a repository. [consumes: repo_meta | produces: labels]")
def list_labels(repo_meta: str) -> str:
    check("list_labels")
    return f"bug, stale for {repo_meta}"


@mcp.tool(description="Add a label to an issue. [consumes: issue | produces: label]")
def add_label(issue: str) -> str:
    check("add_label")
    return f"label added to {issue}"


@mcp.tool(description="Remove a label from an issue. [consumes: labeled_issue | produces: label_removed]")
def remove_label(labeled_issue: str) -> str:
    check("remove_label")
    return f"label removed from {labeled_issue}"


@mcp.tool(description="Set issue priority. [consumes: issue | produces: priority]")
def set_priority(issue: str) -> str:
    check("set_priority")
    return f"priority set on {issue}"


@mcp.tool(description="Search issues by text. [consumes: issue_query | produces: issue_list]")
def search_issues(issue_query: str) -> str:
    check("search_issues")
    return f"issues matching {issue_query}"


@mcp.tool(description="Read an issue history. [consumes: issue | produces: history]")
def issue_history(issue: str) -> str:
    check("issue_history")
    return f"history of {issue}"


@mcp.tool(description="Link two issues. [consumes: issue | produces: link]")
def link_issues(issue: str) -> str:
    check("link_issues")
    return f"linked {issue}"


if __name__ == "__main__":
    mcp.run()
