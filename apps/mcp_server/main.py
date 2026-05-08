import asyncio
import logging
import os
import sys

from apps.mcp_server.server import ThesisMCPServer

logging.basicConfig(level=logging.WARNING)


async def main():
    os.environ.setdefault("DRY_RUN", "true")
    server = ThesisMCPServer()
    server._get_orchestrator()
    print("thesis-assistant ready", file=sys.stderr, flush=True)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
