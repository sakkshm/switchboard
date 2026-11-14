"""Account tools: find an account, read orders, refund."""

from mcp.server.mcpserver import MCPServer

from .common import check

mcp = MCPServer("account", instructions="Billing and account tools.")


@mcp.tool(description="Find an account by email. [consumes: email | produces: account]")
def find_account(email: str) -> str:
    check("find_account")
    return f"account for {email}"


@mcp.tool(description="List orders for an account. [consumes: account | produces: order_id]")
def list_orders(account: str) -> int:
    check("list_orders")
    return 501


@mcp.tool(description="Get an order. [consumes: order_id | produces: order]")
def get_order(order_id: int) -> str:
    check("get_order")
    return f"order {order_id}: duplicate charge"


@mcp.tool(description="Refund a duplicate order. [consumes: order | produces: refund]")
def issue_refund(order: str) -> str:
    check("issue_refund")
    return f"refund issued for {order}"


@mcp.tool(description="Get the invoice for an order. [consumes: order | produces: invoice]")
def get_invoice(order: str) -> str:
    check("get_invoice")
    return f"invoice for {order}"


@mcp.tool(description="List invoices for an account. [consumes: account | produces: invoice_list]")
def list_invoices(account: str) -> str:
    check("list_invoices")
    return f"invoices for {account}"


@mcp.tool(description="Update an account address. [consumes: account, address | produces: account_update]")
def update_address(account: str, address: str) -> str:
    check("update_address")
    return f"address for {account}: {address}"


@mcp.tool(description="Change an account plan. [consumes: account, plan | produces: plan_change]")
def change_plan(account: str, plan: str) -> str:
    check("change_plan")
    return f"plan for {account}: {plan}"


@mcp.tool(description="Add a payment method. [consumes: account, payment | produces: payment_method]")
def add_payment_method(account: str, payment: str) -> str:
    check("add_payment_method")
    return f"payment {payment} on {account}"


@mcp.tool(description="List payments for an account. [consumes: account | produces: payments]")
def list_payments(account: str) -> str:
    check("list_payments")
    return f"payments for {account}"


@mcp.tool(description="Get an account subscription. [consumes: account | produces: subscription]")
def get_subscription(account: str) -> str:
    check("get_subscription")
    return f"subscription for {account}"


@mcp.tool(description="Cancel a subscription. [consumes: subscription | produces: cancellation]")
def cancel_subscription(subscription: str) -> str:
    check("cancel_subscription")
    return f"cancelled {subscription}"


@mcp.tool(description="Apply account credit. [consumes: account, amount | produces: credit]")
def apply_credit(account: str, amount: str) -> str:
    check("apply_credit")
    return f"credit {amount} on {account}"


@mcp.tool(description="Get account balance. [consumes: account | produces: balance]")
def get_balance(account: str) -> str:
    check("get_balance")
    return f"balance for {account}"


@mcp.tool(description="Send a receipt for an order. [consumes: order | produces: receipt]")
def send_receipt(order: str) -> str:
    check("send_receipt")
    return f"receipt for {order}"


if __name__ == "__main__":
    mcp.run()
