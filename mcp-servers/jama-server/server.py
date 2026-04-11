"""Jama MCP Server — placeholder for future Jama REST API integration.

This server is not yet implemented. Jama authentication is currently blocked.
Once resolved, this will expose tools for fetching requirements from Jama.

Planned tools:
- jama_get_projects
- jama_get_items (requirements by project)
- jama_get_item (single requirement)
- jama_get_relationships (links between items)
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jama")

if __name__ == "__main__":
    mcp.run()
