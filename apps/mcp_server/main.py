import asyncio
import os
import logging

from apps.mcp_server.server import ThesisMCPServer

logging.basicConfig(level=logging.WARNING)


async def main():
    os.environ.setdefault("DRY_RUN", "true")
    server = ThesisMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
