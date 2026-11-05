# Развёртывание на Oracle (polling + systemd)

Бот: `python bot.py`, long polling, служба `qarzdorlik-bot`, папка `~/bots/qarzdorlik/`.
Состояние диалогов (FSM) и данные лежат в SQLite `qarzdorlik.db` (таблица `fsm_state`), переживают перезапуск.
Напоминания, ежедневный бэкап админу и чистка состояний идут из фонового цикла внутри бота, внешний cron не нужен.

## Первая установка
1. На ПК: скопировать проект (без `.git`, `.venv`, `tests`) и `.env`:
   ```bash
   ssh -i ~/.ssh/oracle_bots ubuntu@158.101.202.7 "mkdir -p ~/bots/qarzdorlik"
   scp -i ~/.ssh/oracle_bots -r *.py requirements.txt assets handlers locales deploy .env ubuntu@158.101.202.7:~/bots/qarzdorlik/
   ```
2. База со старого хостинга (PythonAnywhere, файл `qarzdorlik.db`): остановить старое веб-приложение, скачать базу (Files или команда `/backup` у админа) и положить рядом:
   ```bash
   scp -i ~/.ssh/oracle_bots qarzdorlik.db ubuntu@158.101.202.7:~/bots/qarzdorlik/
   ```
3. На сервере: `cd ~/bots/qarzdorlik && bash deploy/install_on_server.sh`

## Логи и статус
```bash
sudo systemctl status qarzdorlik-bot
journalctl -u qarzdorlik-bot -f
free -m
```

## Обновление
```bash
scp -i ~/.ssh/oracle_bots -r *.py assets handlers locales ubuntu@158.101.202.7:~/bots/qarzdorlik/
ssh -i ~/.ssh/oracle_bots ubuntu@158.101.202.7 "sudo systemctl restart qarzdorlik-bot"
```
(`.env` и `qarzdorlik.db` не перезаписывать.) Новая зависимость: `~/bots/qarzdorlik/.venv/bin/pip install -r requirements.txt`.

## Откат
- Код: скопировать предыдущую версию из git (`git checkout <коммит>`) и перезапустить службу.
- На PythonAnywhere: включить веб-приложение обратно и вызвать `/setup/<DEPLOY_SECRET>` (старая версия, тег до v3.0.0), а здесь `sudo systemctl stop qarzdorlik-bot`. Одновременно оба варианта запускать нельзя: один токен.
- Бэкап базы: каждый день приходит админу в Telegram, ручной: `/backup`.
