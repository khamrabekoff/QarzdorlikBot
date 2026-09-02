import asyncio
import logging
import sys
import os
import datetime
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import database as db
from handlers import start, debts, admin
from utils import i18n
TOKEN = os.getenv("BOT_TOKEN")

async def check_reminders(bot: Bot):
    # Fetch all debts that are active
    # We filter in Python for complex logic
    try:
        # active_debts = await db.get_active_debts_all() # Incorrect call removed
        # db.get_active_debts filters by user_id. We need GLOBAL check.
        # Let's add get_all_active_debts to database.py or just use raw query here if needed, 
        # but better to stick to db module. 
        # Wait, I didn't add `get_all_active_debts` in previous step.
        # Let's fix that by using a direct query here or adding it.
        # Since I can't edit multiple files at once easily without context switch, 
        # and I just wrote database.py, let's assume I can add a small helper or just do it here if possible.
        # actually I can use db.execute inside main but cleaner to have it in db.
        # I'll add the function `get_all_active_debts` to database.py via a separate tool call if needed, 
        # OR I can just iterate users? No, inefficient.
        # Let's modify database.py first? Or just patch it?
        # Actually I added `get_debts_due_before_or_today` but it was empty/commented logic.
        # Let's use `get_debts_due_before_or_today` which I defined but left with "SELECT * FROM debts WHERE status = 'active'".
        # Yes, that function returns ALL active debts. perfect.
        
        all_debts = await db.get_debts_due_before_or_today("any") 
        
        now = datetime.datetime.now()
        today_str = now.strftime("%d.%m.%Y")
        
        for debt in all_debts:
            # debt is a Row object
            debt_id = debt['id']
            user_id = debt['user_id']
            due_date_str = debt['due_date']
            created_at_str = debt['created_at'] # "2023-XX-XX HH:MM:SS"
            last_reminded_at = debt['last_reminded_at'] # "2023-XX-XX HH:MM:SS" or None
            
            # Parse Dates
            try:
                due_date = datetime.datetime.strptime(due_date_str, "%d.%m.%Y")
            except ValueError:
                continue # Bad data
                
            # Parse Created At
            # SQLite default CURRENT_TIMESTAMP is YYYY-MM-DD HH:MM:SS
            try:
                created_at = datetime.datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                 # Fallback if weird format
                 created_at = now
            
            # Logic 1: Has it been reminded today?
            if last_reminded_at:
                try:
                    last_reminded = datetime.datetime.strptime(last_reminded_at, "%Y-%m-%d %H:%M:%S")
                    if last_reminded.date() == now.date():
                        continue # Already reminded today
                except ValueError:
                    pass

            should_send = False
            
            # Rule: Due Date Logic
            # "If 10:00 AM standard"
            # We assume "Due Date" means "Remind on this day".
            # If due_date <= today, we rely on reminder logic.
            # But wait, User said: "If specified TODAY, then after 30 mins".
            
            is_due_today = (due_date.date() == now.date())
            
            if is_due_today:
                # Special Case: Created Today AND Due Today
                if created_at.date() == now.date():
                    # Check if 30 mins passed
                    if now > created_at + datetime.timedelta(minutes=30):
                         should_send = True
                else:
                    # Created previously, due today. Standard 10 AM.
                    if now.hour >= 10:
                        should_send = True
            elif due_date.date() < now.date():
                 # Overdue. Remind at 10 AM daily.
                 if now.hour >= 10:
                     should_send = True
            
            if should_send:
                # Send it
                user = await db.get_user(user_id)
                if not user: continue
                lang = user[2]
                
                msg_key = "reminder_msg" if debt['debt_type'] == 'lent' else "reminder_msg_i_owe"
                text = i18n.get(msg_key, lang, 
                                name=debt['person_name'], 
                                amount=debt['amount'], 
                                currency=debt['currency'])
                
                try:
                    await bot.send_message(user_id, text, parse_mode="HTML")
                    # Update last_reminded
                    await db.update_last_reminded(debt_id, now.strftime("%Y-%m-%d %H:%M:%S"))
                except Exception as e:
                    logging.error(f"Failed to send reminder to {user_id}: {e}")

    except Exception as e:
        logging.error(f"Error in check_reminders: {e}")

async def main():
    global TOKEN
    if not TOKEN:
        print("Bot Token not found in environment variables.")
        TOKEN = input("Please enter your Telegram Bot Token: ").strip()
    
    # PythonAnywhere Proxy Check
    session = None
    if os.getenv("PYTHONANYWHERE_DOMAIN"):
        from aiogram.client.session.aiohttp import AiohttpSession
        session = AiohttpSession(proxy="http://proxy.server:3128")
        logging.info("Using PythonAnywhere proxy")

    bot = Bot(token=TOKEN, session=session)
    dp = Dispatcher()
    
    dp.include_router(start.router)
    dp.include_router(debts.router)
    dp.include_router(admin.router)
    
    await db.init_db()
    
    scheduler = AsyncIOScheduler()
    # Run every 5 minutes to catch the "30 min after creation" window fairly accurately
    scheduler.add_job(check_reminders, 'interval', minutes=5, args=[bot]) 
    scheduler.start()
    
    # Startup check
    await check_reminders(bot)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    logging.info("Bot started!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
