import asyncio
import logging

from apps.mcp_server.server import ThesisMCPServer
from core.logging import configure_logging


async def main():
    configure_logging(level=logging.WARNING)
    server = ThesisMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
