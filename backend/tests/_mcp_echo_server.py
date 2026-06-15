"""Minimal in-process MCP server for the MCP-client integration test.

Launched over stdio by test_mcp_client.py — exposes a single deterministic tool
so the client wiring (discovery → registry → call) can be verified without npx
or a browser.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run()
