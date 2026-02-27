
import asyncio
import sys
import os

# Add the parent directory to sys.path to resolve 'app' module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import server_utils, database
from app.config import settings

async def main():
    print(f"Connecting to {settings.DATABASE_URL}...")
    async with database.AsyncSessionLocal() as db:
        print("Initializing default modules...")
        await server_utils.init_default_modules(db)
        print("Modules initialized.")
        
        # Verify
        modules = await server_utils.get_active_modules(db)
        print(f"Active modules: {[m.name for m in modules]}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
