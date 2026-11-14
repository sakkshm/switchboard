"""Repository tools: the first hop of the issue and review chains."""

from mcp.server.mcpserver import MCPServer

from .common import check

mcp = MCPServer("repo", instructions="Repository and code review tools.")


@mcp.tool(description="Find a repository by name. [consumes: repo | produces: repo_meta]")
def repo_search(repo: str) -> str:
    check("repo_search")
    return f"{repo}@main"


@mcp.tool(description="List open issues in a repository. [consumes: repo_meta | produces: issue_number]")
def list_issues(repo_meta: str, state: str = "open") -> int:
    check("list_issues")
    return 42 if state == "open" else 7


@mcp.tool(description="Locate a source file in a repository. [consumes: repo, path | produces: file_path]")
def locate_file(repo: str, path: str) -> str:
    check("locate_file")
    return f"{repo}/{path}"


@mcp.tool(description="Read a source file. [consumes: file_path | produces: file]")
def read_file(file_path: str) -> str:
    check("read_file")
    return f"contents of {file_path}"


@mcp.tool(description="Get the diff for a changed file. [consumes: file | produces: diff]")
def get_diff(file: str) -> str:
    check("get_diff")
    return f"diff for {file}"


@mcp.tool(description="Post a code review on a patch. [consumes: diff, message | produces: review]")
def post_review(diff: str, message: str) -> str:
    check("post_review")
    return f"review posted: {message}"


@mcp.tool(description="List repositories in an organization. [consumes: org | produces: repo_list]")
def list_repos(org: str) -> str:
    check("list_repos")
    return f"{org}/api, {org}/web"


@mcp.tool(description="Search code for a symbol. [consumes: code_query | produces: match]")
def search_code(code_query: str) -> str:
    check("search_code")
    return f"match for {code_query}"


@mcp.tool(description="List branches in a repository. [consumes: repo_meta | produces: branch]")
def list_branches(repo_meta: str) -> str:
    check("list_branches")
    return f"{repo_meta}:main, payments"


@mcp.tool(description="Get the latest commit on a branch. [consumes: branch | produces: commit]")
def get_commit(branch: str) -> str:
    check("get_commit")
    return f"commit on {branch}"


@mcp.tool(description="List pull requests. [consumes: repo_meta | produces: pull_request]")
def list_pull_requests(repo_meta: str) -> int:
    check("list_pull_requests")
    return 17


@mcp.tool(description="Get a pull request. [consumes: pull_request | produces: pr_detail]")
def get_pull_request(pull_request: int) -> str:
    check("get_pull_request")
    return f"PR #{pull_request}"


@mcp.tool(description="Merge a pull request. [consumes: pr_detail | produces: merge]")
def merge_pull_request(pr_detail: str) -> str:
    check("merge_pull_request")
    return f"merged {pr_detail}"


@mcp.tool(description="List files in a directory. [consumes: dir | produces: file_list]")
def list_files(dir: str) -> str:
    check("list_files")
    return f"src/app.py, {dir}/README.md"


@mcp.tool(description="Get repository statistics. [consumes: repo_meta | produces: stats]")
def repo_stats(repo_meta: str) -> str:
    check("repo_stats")
    return f"stats for {repo_meta}"


if __name__ == "__main__":
    mcp.run()
