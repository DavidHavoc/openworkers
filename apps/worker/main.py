import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    logger.info("Worker process starting. Awaiting async tasks...")
    # In a real app, this might connect to a Redis queue or Celery broker
    while True:
        await asyncio.sleep(10)
        logger.debug("Worker heartbeat...")


if __name__ == "__main__":
    asyncio.run(main())
