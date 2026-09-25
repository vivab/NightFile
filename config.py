import logging
import os

# --- Обязательные переменные окружения (задаются в панели ботхоста) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


def _parse_admin_ids(raw: str) -> set[int]:
    """
    Разбирает ADMIN_IDS в набор чисел. Раньше здесь был int(x) без защиты:
    если в переменной оказывался пробел, точка с запятой вместо запятой,
    случайный лишний символ и т.п. — весь модуль падал на импорте,
    и из-за этого не запускался вообще весь бот (все команды молчали,
    включая /start и /admin). Теперь битые значения просто пропускаются
    с предупреждением в лог, а не роняют бота целиком.
    """
    ids = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logging.warning(f"ADMIN_IDS: не могу распознать '{part}' как число, пропускаю его")
    return ids


# Список Telegram ID админов через запятую, например: "123456789,987654321"
ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

# Путь к базе данных. По умолчанию /app/data/bot.db —
# именно эту папку нужно подключить как persistent volume на хостинге,
# иначе база будет обнуляться при каждом деплое/рестарте.
DB_PATH = os.getenv("DB_PATH", "/app/data/bot.db")

