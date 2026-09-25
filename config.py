import os

# --- Обязательные переменные окружения (задаются в панели ботхоста) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Список Telegram ID админов через запятую, например: "123456789,987654321"
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Путь к базе данных. По умолчанию /app/data/bot.db —
# именно эту папку нужно подключить как persistent volume на хостинге,
# иначе база будет обнуляться при каждом деплое/рестарте.
DB_PATH = os.getenv("DB_PATH", "/app/data/bot.db")
