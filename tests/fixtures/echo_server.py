"""Echo fixture: one two-step chain, used to test the gateway skeleton."""

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

mcp = MCPServer("echo", instructions="Two tools that pass a value along.")


class Echoed(BaseModel):
    echoed: str


class Playback(BaseModel):
    playback: str


@mcp.tool(description="Echo a value. [consumes: value | produces: echoed]")
def echo_a(value: str) -> Echoed:
    return Echoed(echoed=value.upper())


@mcp.tool(description="Play back an echoed value. [consumes: echoed | produces: playback]")
def echo_b(echoed: str) -> Playback:
    return Playback(playback=f"playback: {echoed}")


if __name__ == "__main__":
    mcp.run()
