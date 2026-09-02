import os
import asyncio
import logging
import json
from flask import Flask, request
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
from handlers import start, debts, admin
from main import check_reminders

load_dotenv()

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# App configuration
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = "/webhook"

# PythonAnywhere Proxy Check
session = None
if os.getenv("PYTHONANYWHERE_DOMAIN"):
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(proxy="http://proxy.server:3128")

# Initialize bot and dp
bot = Bot(token=TOKEN, session=session)
dp = Dispatcher()

# Include routers
dp.include_router(start.router)
dp.include_router(debts.router)
dp.include_router(admin.router)

app = Flask(__name__)

# Create a persistent event loop for this worker process
worker_loop = asyncio.new_event_loop()
asyncio.set_event_loop(worker_loop)

# Initialize everything once
try:
    worker_loop.run_until_complete(db.init_db())
    scheduler = AsyncIOScheduler(event_loop=worker_loop)
    scheduler.add_job(check_reminders, 'interval', minutes=5, args=[bot])
    scheduler.start()
    logger.info("Database and Scheduler initialized in Webhook worker.")
except Exception as e:
    logger.error(f"Initialization error: {e}", exc_info=True)

# Entry point for Telegram updates
@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        try:
            json_string = request.get_data().decode('utf-8')
            logger.info(f"Incoming update: {json_string}")
            
            update = Update.model_validate_json(json_string)
            
            # Feed update to dispatcher using the persistent worker loop
            worker_loop.run_until_complete(dp.feed_update(bot, update))
            
            return 'OK', 200
        except Exception as e:
            logger.error(f"Webhook processing error: {e}", exc_info=True)
            return 'Error', 500
    
    return 'Forbidden', 403

if __name__ == "__main__":
    # Local testing
    app.run(port=5000)
