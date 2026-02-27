from app.database import init_db
from app.config import settings
import asyncio

async def test_db():
    print(f"DATABASE_URL: {settings.DATABASE_URL}")
    try:
        await init_db()
        print("DB Initialized Successfully!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_db())
