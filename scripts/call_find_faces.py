#!/usr/bin/env python3
"""Call get_mounting_features via the FreeCAD MCP server for local debugging."""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    """Invoke get_mounting_features and print the structured response."""
    face = sys.argv[1] if len(sys.argv) > 1 else "Face19"

    server_params = StdioServerParameters(
        command=".venv/bin/freecad-mcp",
        env={
            **os.environ,
            "FREECAD_MODE": "xmlrpc",
            "FREECAD_SOCKET_HOST": "localhost",
            "FREECAD_XMLRPC_PORT": "9875",
        },
    )

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        result = await session.call_tool(
            "get_mounting_features",
            {
                "object_name": "Part__Feature",
                "face": face,
            },
        )

        payload = result.structuredContent
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        if payload:
            print("\nSummary")
            print(f"  face: {payload.get('face')}")
            print(f"  surface_type: {payload.get('surface_type')}")
            print(f"  face_center: {payload.get('face_center')}")
            print(f"  face_normal: {payload.get('face_normal')}")
            print(f"  unique_hole_count: {payload.get('unique_hole_count')}")
            print(f"  cylindrical_face_count: {payload.get('cylindrical_face_count')}")
            print(f"  hole_array_center: {payload.get('hole_array_center')}")
            print(f"  axis_candidates: {payload.get('axis_candidates')}")


if __name__ == "__main__":
    asyncio.run(main())
