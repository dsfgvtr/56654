import os
import sqlite3
import asyncio
import logging
from datetime import datetime

from maxapi import Bot, Dispatcher, F
from maxapi.types import (
    BotStarted, MessageCreated, MessageCallback,
    Command, CommandStart, CallbackButton,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# ============ НАСТРОЙКА ============
# MAX_BOT_TOKEN Bothost подставляет сам.
# ADMIN_IDS задаётся в панели Bothost (переменные окружения).
TOKEN = os.environ.get("MAX_BOT_TOKEN") or os.environ.get("MAX_TOKEN") or ""
if not TOKEN:
    raise SystemExit("Нет токена: задай MAX_BOT_TOKEN в переменных окружения Bothost")

ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diary")

db = sqlite3.connect("diary.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'student');
CREATE TABLE IF NOT EXISTS schedule(
    id INTEGER PRIMARY KEY AUTOINCREMENT, weekday TEXT, lesson_num INTEGER,
    subject TEXT, room TEXT, time TEXT);
CREATE TABLE IF NOT EXISTS grades(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, subject TEXT,
    grade TEXT, date TEXT);
CREATE TABLE IF NOT EXISTS homework(
    id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, task TEXT,
    due_date TEXT, done INTEGER DEFAULT 0);
""")
db.commit()

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

# ============ ПОЛЬЗОВАТЕЛИ И ДОСТУП ============
def get_uid(event):
    """ID пользователя. Для кнопок — кто нажал, а не кто отправил сообщение."""
    cb = getattr(event, "callback", None)
    if cb is not None:
        user = getattr(cb, "user", None) or getattr(cb, "sender", None)
        if user is not None and getattr(user, "user_id", None) is not None:
            return user.user_id
    msg = getattr(event, "message", None)
    if msg is not None and getattr(msg, "sender", None) is not None:
        return msg.sender.user_id
    u = getattr(event, "user", None)  # BotStarted
    if u is not None and getattr(u, "user_id", None) is not None:
        return u.user_id
    return None

def user_role(uid):
    """Бот доступен всем: кто не админ — тот ученик."""
    if uid is None:
        return None
    row = db.execute("SELECT role FROM users WHERE user_id=?", (uid,)).fetchone()
    if row:
        return row[0]
    return "admin" if uid in ADMIN_IDS else "student"

def guard(handler):
    """Пускает всех, пишет лог действий."""
    async def wrapper(event, *args, **kwargs):
        uid = get_uid(event)
        logger.info("id=%s (%s) -> %s", uid, user_role(uid), type(event).__name__)
        return await handler(event)
    return wrapper

# ============ КЛАВИАТУРА ============
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="📅 Расписание", payload="sched"))
    kb.row(CallbackButton(text="📝 Домашка", payload="hw"))
    kb.row(CallbackButton(text="🎓 Мои оценки", payload="grades"))
    return kb.as_markup()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ============ СТАРТ И МЕНЮ ============
@dp.bot_started()
@guard
async def bot_started(event: BotStarted):
    await bot.send_message(chat_id=event.chat_id,
                           text="Дневник запущен 🎒 Отправь /start, чтобы открыть меню.")

@dp.message_created(CommandStart())
@guard
async def start(event: MessageCreated):
    uid = get_uid(event)
    # админа из ADMIN_IDS записываем в базу при первом /start
    if user_role(uid) == "admin" and not db.execute(
        "SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
        db.execute("INSERT INTO users VALUES (?,?, 'admin')",
                   (uid, event.message.sender.username or ""))
        db.commit()
    await event.message.answer(
        f"Привет, {event.message.sender.first_name}! Это твой электронный дневник 🎒",
        attachments=[main_menu_kb()],
    )

# ============ КНОПКИ (доступны всем) ============
@dp.message_callback(F.callback.payload == "sched")
@guard
async def show_schedule(event: MessageCallback):
    today = WEEKDAYS[datetime.now().weekday()] if datetime.now().weekday() < 6 else None
    out = []
    for day in WEEKDAYS:
        rows = db.execute(
            "SELECT lesson_num, subject, room, time FROM schedule WHERE weekday=? ORDER BY lesson_num",
            (day,)).fetchall()
        if rows:
            mark = " 👈 сегодня" if day == today else ""
            out.append(f"{day}{mark}\n" + "\n".join(
                f"{r[0]}. {r[1]} (каб. {r[2] or '—'}, {r[3] or ''})" for r in rows))
    await event.message.answer("\n\n".join(out) or "Расписание пустое — попроси админа заполнить.")
    await event.answer(notification="Готово")

@dp.message_callback(F.callback.payload == "hw")
@guard
async def show_hw(event: MessageCallback):
    rows = db.execute("SELECT subject, task, due_date FROM homework WHERE done=0").fetchall()
    await event.message.answer("\n".join(f"• {r[0]} (до {r[2]}): {r[1]}" for r in rows)
                               or "Домашки нет 🎉")
    await event.answer(notification="Готово")

@dp.message_callback(F.callback.payload == "grades")
@guard
async def show_grades(event: MessageCallback):
    uid = get_uid(event)
    rows = db.execute("SELECT subject, grade, date FROM grades WHERE user_id=? ORDER BY date",
                      (uid,)).fetchall()
    await event.message.answer("\n".join(f"{r[0]}: {r[1]} ({r[2]})" for r in rows)
                               or "Оценок пока нет.")
    await event.answer(notification="Готово")

# ============ КОМАНДЫ АДМИНА (закрыты для остальных) ============
@dp.message_created(Command("add_lesson"))
@guard
async def add_lesson(event: MessageCreated):
    if user_role(get_uid(event)) != "admin":
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()[1:]
    if len(p) != 5 or p[0].capitalize() not in WEEKDAYS:
        return await event.message.answer("Формат: /add_lesson Понедельник 1 Алгебра 204 08:30")
    db.execute("INSERT INTO schedule(weekday, lesson_num, subject, room, time) VALUES (?,?,?,?,?)",
               (p[0].capitalize(), int(p[1]), p[2], p[3], p[4]))
    db.commit()
    await event.message.answer("✅ Урок добавлен.")

@dp.message_created(Command("add_hw"))
@guard
async def add_hw(event: MessageCreated):
    if user_role(get_uid(event)) != "admin":
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split(maxsplit=3)[1:]
    if len(p) != 3:
        return await event.message.answer("Формат: /add_hw Алгебра 2026-11-15 Повторить формулы")
    db.execute("INSERT INTO homework(subject, task, due_date) VALUES (?,?,?)", p)
    db.commit()
    await event.message.answer("✅ Домашка добавлена.")

@dp.message_created(Command("grade"))
@guard
async def put_grade(event: MessageCreated):
    if user_role(get_uid(event)) != "admin":
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()[1:]
    if len(p) != 3:
        return await event.message.answer("Формат: /grade <user_id> <Предмет> <Оценка>")
    db.execute("INSERT INTO grades(user_id, subject, grade, date) VALUES (?,?,?,?)",
               (int(p[0]), p[1], p[2], datetime.now().strftime("%Y-%m-%d")))
    db.commit()
    await event.message.answer("✅ Оценка поставлена.")

@dp.message_created(Command("grant"))
@guard
async def grant(event: MessageCreated):
    if user_role(get_uid(event)) != "admin":
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()[1:]
    if len(p) != 2 or p[1] not in ("student", "admin"):
        return await event.message.answer("Формат: /grant <user_id> <student|admin>")
    db.execute("INSERT OR REPLACE INTO users VALUES (?,?,?)", (int(p[0]), "", p[1]))
    db.commit()
    await event.message.answer("✅ Доступ настроен.")

# ============ FALLBACK ============
@dp.message_created()
@guard
async def fallback(event: MessageCreated):
    if event.message.body.text:
        await event.message.answer("Не понял 🤔 Нажми /start, чтобы открыть меню.")

async def main():
    # Снимаем webhook-подписки: пока они висят, polling не получает события
    try:
        await bot.delete_webhook()
        logger.info("Webhook-подписки сняты, запускаю polling")
    except Exception as e:
        logger.warning("Не удалось снять подписки (возможно, их нет): %s", e)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
