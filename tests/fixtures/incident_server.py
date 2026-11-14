"""Incident tools: logs, traces, tickets, and on-call paging."""

from mcp.server.mcpserver import MCPServer

from .common import check

mcp = MCPServer("incident", instructions="Incident response tools.")


@mcp.tool(description="Search logs for a service. [consumes: service | produces: log_ref]")
def search_logs(service: str, window: str = "1h") -> str:
    check("search_logs")
    return f"log:{service}:{window}:timeout"


@mcp.tool(description="Get the trace for a log entry. [consumes: log_ref | produces: trace]")
def get_trace(log_ref: str) -> str:
    check("get_trace")
    return f"trace for {log_ref}"


@mcp.tool(description="Open a ticket for a trace. [consumes: trace | produces: ticket]")
def open_ticket(trace: str) -> str:
    check("open_ticket")
    return f"ticket for {trace}"


@mcp.tool(description="Notify a channel about a ticket. [consumes: ticket, channel | produces: notification]")
def notify_channel(ticket: str, channel: str) -> str:
    check("notify_channel")
    return f"notified {channel} about {ticket}"


@mcp.tool(description="Find the on-call engineer for a trace. [consumes: trace | produces: oncall]")
def find_oncall(trace: str) -> str:
    check("find_oncall")
    return f"on-call for {trace}: ada"


@mcp.tool(description="Page the on-call engineer. [consumes: oncall, message | produces: page]")
def page_oncall(oncall: str, message: str) -> str:
    check("page_oncall")
    return f"paged {oncall}: {message}"


@mcp.tool(description="List services owned by a team. [consumes: team | produces: service_list]")
def list_services(team: str) -> str:
    check("list_services")
    return f"checkout, payments for {team}"


@mcp.tool(description="Get service metrics. [consumes: service | produces: metrics]")
def get_metrics(service: str) -> str:
    check("get_metrics")
    return f"metrics for {service}"


@mcp.tool(description="Get active alerts for a service. [consumes: service | produces: alerts]")
def get_alerts(service: str) -> str:
    check("get_alerts")
    return f"alerts for {service}"


@mcp.tool(description="Acknowledge an alert. [consumes: alerts | produces: ack]")
def acknowledge_alert(alerts: str) -> str:
    check("acknowledge_alert")
    return f"acknowledged {alerts}"


@mcp.tool(description="Resolve an incident ticket. [consumes: ticket | produces: resolved]")
def resolve_incident(ticket: str) -> str:
    check("resolve_incident")
    return f"resolved {ticket}"


@mcp.tool(description="Add a note to an incident. [consumes: ticket | produces: note]")
def add_incident_note(ticket: str) -> str:
    check("add_incident_note")
    return f"note on {ticket}"


@mcp.tool(description="Escalate an incident. [consumes: ticket | produces: escalation]")
def escalate_incident(ticket: str) -> str:
    check("escalate_incident")
    return f"escalated {ticket}"


@mcp.tool(description="Get the runbook for a service. [consumes: service | produces: runbook]")
def get_runbook(service: str) -> str:
    check("get_runbook")
    return f"runbook for {service}"


@mcp.tool(description="List incidents for a team. [consumes: team | produces: incident_list]")
def list_incidents(team: str) -> str:
    check("list_incidents")
    return f"open incidents for {team}"


if __name__ == "__main__":
    mcp.run()
