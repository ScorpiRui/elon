import asyncio
import logging
from utils import scheduler_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def main():
    # runs forever
    await scheduler_loop()

if __name__ == "__main__":
    asyncio.run(main())
