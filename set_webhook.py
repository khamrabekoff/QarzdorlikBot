import asyncio
import os
from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
# Replace 'username' with your actual PythonAnywhere username
USERNAME = "your_username" 
WEBHOOK_URL = f"https://{USERNAME}.pythonanywhere.com/webhook"

async def main():
    # PythonAnywhere Proxy Check
    session = None
    if os.getenv("PYTHONANYWHERE_DOMAIN"):
        from aiogram.client.session.aiohttp import AiohttpSession
        session = AiohttpSession(proxy="http://proxy.server:3128")
        print("Using PythonAnywhere proxy...")

    bot = Bot(token=TOKEN, session=session)
    print(f"Setting webhook to: {WEBHOOK_URL}...")
    
    success = await bot.set_webhook(WEBHOOK_URL)
    
    if success:
        print("✅ Webhook successfully set!")
        print(f"Your bot is now listening at {WEBHOOK_URL}")
    else:
        print("❌ Failed to set webhook.")
    
    await bot.session.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        USERNAME = sys.argv[1]
        WEBHOOK_URL = f"https://{USERNAME}.pythonanywhere.com/webhook"
    else:
        print("Usage: python set_webhook.py YOUR_PYTHONANYWHERE_USERNAME")
        sys.exit(1)
        
    asyncio.run(main())
