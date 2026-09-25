import os
import aiosqlite

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    file_id     TEXT NOT NULL,
    file_type   TEXT NOT NULL,   -- document / photo / video / audio
    caption     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id  INTEGER PRIMARY KEY,
    joined_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


_EXPECTED_COLUMNS = {
    "files": {"id", "title", "file_id", "file_type", "caption"},
    "settings": {"key", "value"},
    "users": {"user_id", "joined_at"},
}


async def _migrate(db: aiosqlite.Connection) -> None:
    """
    Если на диске остался файл bot.db от старой/битой версии схемы
    (например, из-за прежних неудачных деплоев), пересоздаём именно
    те таблицы, чья структура не совпадает с ожидаемой.
    Это безопасно на этапе разработки, когда в базе ещё нет важных данных.
    """
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    existing_tables = {row[0] for row in await cur.fetchall()}

    for table, expected_cols in _EXPECTED_COLUMNS.items():
        if table not in existing_tables:
            continue
        cur = await db.execute(f"PRAGMA table_info({table})")
        actual_cols = {row[1] for row in await cur.fetchall()}
        if actual_cols != expected_cols:
            await db.execute(f"DROP TABLE {table}")

    await db.commit()


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await _migrate(db)
        await db.executescript(_SCHEMA)
        await db.commit()


# ---------- settings (канал, тексты кнопок и т.п.) ----------

async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_setting(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


# ---------- files ----------

async def add_file(title: str, file_id: str, file_type: str, caption: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO files (title, file_id, file_type, caption) VALUES (?, ?, ?, ?)",
            (title, file_id, file_type, caption),
        )
        await db.commit()
        return cur.lastrowid


async def get_file(file_db_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, title, file_id, file_type, caption FROM files WHERE id = ?",
            (file_db_id,),
        )
        return await cur.fetchone()


async def list_files():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title FROM files ORDER BY id DESC")
        return await cur.fetchall()


async def delete_file(file_db_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM files WHERE id = ?", (file_db_id,))
        await db.commit()


# ---------- users (для /stats) ----------

async def add_user(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
        )
        await db.commit()


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
        return row[0] if row else 0
