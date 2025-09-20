
import asyncio
import logging
from utils import ensure_scheduler_running

logging.basicConfig(level=logging.INFO)

async def main():
    logging.info("Broadcast worker starting…")
    await ensure_scheduler_running()
    # keep process alive forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
