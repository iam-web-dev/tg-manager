
import asyncio
from telethon import TelegramClient
from config import settings

async def main():
    session_path = settings.get_session_path()

    client = TelegramClient(
        session_path,
        settings.API_ID,
        settings.API_HASH
    )

    await client.start()

    me = await client.get_me()
    print("\n" + "=" * 40)
    print("LOGIN MUVAFFAQIYATLI!")
    print(f"ACCOUNT: {me.first_name} {me.last_name or ''} (@{me.username})")
    print(f"USER ID: {me.id}")
    print(f"SESSION FILE: {session_path}.session")
    print("=" * 40 + "\n")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
