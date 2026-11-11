import os
import sqlite3
import asyncio
import random
import logging
from datetime import datetime, timedelta

from maxapi import Bot, Dispatcher, F
from maxapi.types import (
    BotStarted, MessageCreated, MessageCallback,
    Command, CommandStart, CallbackButton,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# ============ НАСТРОЙКА ============
TOKEN = os.environ.get("MAX_BOT_TOKEN") or os.environ.get("MAX_TOKEN") or ""
if not TOKEN:
    raise SystemExit("Нет токена: задай MAX_BOT_TOKEN в переменных окружения Bothost")

ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diary")

# ============ БАЗА ДАННЫХ ============
db = sqlite3.connect("diary.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'student',
    study_group TEXT);
CREATE TABLE IF NOT EXISTS schedule(
    id INTEGER PRIMARY KEY AUTOINCREMENT, weekday TEXT, lesson_num INTEGER,
    subject TEXT, room TEXT, time TEXT, group_name TEXT);
CREATE TABLE IF NOT EXISTS grades(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, subject TEXT,
    grade TEXT, date TEXT);
CREATE TABLE IF NOT EXISTS homework(
    id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, task TEXT,
    due_date TEXT, done INTEGER DEFAULT 0, group_name TEXT);
CREATE TABLE IF NOT EXISTS auth(
    user_id INTEGER PRIMARY KEY, phone TEXT, approved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS codes(
    user_id INTEGER PRIMARY KEY, phone TEXT, code TEXT, created TEXT);
""")
db.commit()

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]

# ============ ПОЛЬЗОВАТЕЛИ И ДОСТУП ============
def get_uid(event):
    cb = getattr(event, "callback", None)
    if cb is not None:
        user = getattr(cb, "user", None) or getattr(cb, "sender", None)
        if user is not None and getattr(user, "user_id", None) is not None:
            return user.user_id
    msg = getattr(event, "message", None)
    if msg is not None and getattr(msg, "sender", None) is not None:
        return msg.sender.user_id
    u = getattr(event, "user", None)
    if u is not None and getattr(u, "user_id", None) is not None:
        return u.user_id
    return None

def is_admin(uid):
    if uid in ADMIN_IDS:
        return True
    row = db.execute("SELECT role FROM users WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0] == "admin")

def is_approved(uid):
    if is_admin(uid):
        return True
    row = db.execute("SELECT approved FROM auth WHERE user_id=?", (uid,)).fetchone()
    return bool(row and row[0] == 1)

def get_group(uid):
    row = db.execute("SELECT study_group FROM users WHERE user_id=?", (uid,)).fetchone()
    return row[0] if row else None

def guard(handler):
    """Кнопки только для авторизованных. Неавторизованных мягко отправляем на /login."""
    async def wrapper(event):
        uid = get_uid(event)
        if not is_approved(uid):
            logger.warning("Не авторизован: id=%s", uid)
            msg = getattr(event, "message", None)
            if msg is not None:
                await msg.answer("🔒 Сначала авторизуйтесь: /login +7XXXXXXXXXX")
            return
        logger.info("id=%s -> %s", uid, type(event).__name__)
        return await handler(event)
    return wrapper

async def notify_admins(text):
    """Шлём всем админам. Админ должен хотя бы раз написать боту /start."""
    targets = set(ADMIN_IDS)
    for (uid,) in db.execute("SELECT user_id FROM users WHERE role='admin'").fetchall():
        targets.add(uid)
    for uid in targets:
        try:
            await bot.send_message(chat_id=uid, text=text)
        except Exception as e:
            logger.warning("Не удалось отправить админу %s: %s", uid, e)

# ============ КЛАВИАТУРА ============
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="📅 Расписание", payload="sched"))
    kb.row(CallbackButton(text="📝 Домашка", payload="hw"))
    kb.row(CallbackButton(text="🎓 Мои оценки", payload="grades"))
    return kb.as_markup()

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ============ СТАРТ ============
@dp.bot_started()
async def bot_started(event: BotStarted):
    await bot.send_message(chat_id=event.chat_id,
                           text="Дневник запущен 🎒 Отправь /start.")

@dp.message_created(CommandStart())
async def start(event: MessageCreated):
    uid = get_uid(event)
    if is_admin(uid) and not db.execute(
        "SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
        db.execute("INSERT INTO users(user_id, username, role) VALUES (?,?, 'admin')",
                   (uid, event.message.sender.username or ""))
        db.commit()
    if is_approved(uid):
        await event.message.answer(
            f"Привет, {event.message.sender.first_name}! Это твой электронный дневник 🎒",
            attachments=[main_menu_kb()])
    else:
        await event.message.answer(
            "🔒 Это закрытый дневник.\n"
            "Чтобы получить доступ, отправьте:\n"
            "/login +7XXXXXXXXXX\n\n"
            "Когда админ подтвердит заявку, он передаст вам код — "
            "введите его командой /code <код>.")

# ============ АВТОРИЗАЦИЯ ============
@dp.message_created(Command("login"))
async def login(event: MessageCreated):
    uid = get_uid(event)
    if is_approved(uid):
        return await event.message.answer("Вы уже авторизованы ✅",
                                          attachments=[main_menu_kb()])
    p = event.message.body.text.split()
    if len(p) != 2 or not p[1].startswith("+7") or len(p[1]) != 12:
        return await event.message.answer("Формат: /login +79001234567")
    phone = p[1]
    code = f"{random.randint(1000, 9999)}"
    db.execute("INSERT OR REPLACE INTO codes VALUES (?,?,?,?)",
               (uid, phone, code, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    name = event.message.sender.first_name or "Пользователь"
    await event.message.answer(
        "✅ Заявка отправлена. Когда админ подтвердит, он передаст вам код.\n"
        "Введите его командой: /code <код>")
    await notify_admins(
        f"🔔 Заявка на доступ!\n"
        f"Имя: {name} | ID: {uid} | Телефон: {phone}\n"
        f"Код для выдачи: {code}\n\n"
        f"Передайте код ученику или подтвердите сами: /approve {uid}")

@dp.message_created(Command("code"))
async def check_code(event: MessageCreated):
    uid = get_uid(event)
    p = event.message.body.text.split()
    if len(p) != 2:
        return await event.message.answer("Формат: /code 1234")
    row = db.execute("SELECT phone, code, created FROM codes WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return await event.message.answer("Код не найден. Сначала отправьте /login +7XXXXXXXXXX")
    phone, code, created = row
    t = datetime.strptime(created, "%Y-%m-%d %H:%M")
    if datetime.now() - t > timedelta(minutes=10):
        db.execute("DELETE FROM codes WHERE user_id=?", (uid,))
        db.commit()
        return await event.message.answer("Код истёк. Отправьте /login заново.")
    if p[1] != code:
        return await event.message.answer("❌ Неверный код.")
    db.execute("INSERT OR REPLACE INTO auth VALUES (?,?,1)", (uid, phone))
    db.execute("DELETE FROM codes WHERE user_id=?", (uid,))
    db.commit()
    await event.message.answer("✅ Доступ открыт! Не забудьте выбрать группу: /set_group <номер группы>",
                               attachments=[main_menu_kb()])

@dp.message_created(Command("approve"))
async def approve(event: MessageCreated):
    uid = get_uid(event)
    if not is_admin(uid):
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()
    if len(p) != 2 or not p[1].lstrip("-").isdigit():
        return await event.message.answer("Формат: /approve <user_id из заявки>")
    target = int(p[1])
    row = db.execute("SELECT phone FROM codes WHERE user_id=?", (target,)).fetchone()
    phone = row[0] if row else "не указан"
    db.execute("INSERT OR REPLACE INTO auth VALUES (?,?,1)", (target, phone))
    db.execute("DELETE FROM codes WHERE user_id=?", (target,))
    db.commit()
    await event.message.answer(f"✅ Пользователь {target} подтверждён.")
    try:
        await bot.send_message(chat_id=target,
                               text="✅ Админ подтвердил ваш доступ! Отправьте /start.",
                               attachments=[main_menu_kb()])
    except Exception:
        pass

@dp.message_created(Command("set_group"))
async def set_group(event: MessageCreated):
    uid = get_uid(event)
    if not is_approved(uid):
        return await event.message.answer("🔒 Сначала авторизуйтесь: /login +7XXXXXXXXXX")
    p = event.message.body.text.split()
    if len(p) != 2:
        return await event.message.answer("Формат: /set_group ПКС-21")
    row = db.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
    if row:
        db.execute("UPDATE users SET study_group=? WHERE user_id=?", (p[1], uid))
    else:
        db.execute("INSERT INTO users(user_id, username, role, study_group) VALUES (?,?,'student',?)",
                   (uid, event.message.sender.username or "", p[1]))
    db.commit()
    await event.message.answer(f"✅ Ваша группа: {p[1]}")

# ============ КНОПКИ ============
@dp.message_callback(F.callback.payload == "sched")
@guard
async def show_schedule(event: MessageCallback):
    uid = get_uid(event)
    grp = get_group(uid)
    if not grp:
        return await event.message.answer("Сначала выберите группу: /set_group ПКС-21")
    today = WEEKDAYS[datetime.now().weekday()] if datetime.now().weekday() < 6 else None
    out = []
    for day in WEEKDAYS:
        rows = db.execute(
            "SELECT lesson_num, subject, room, time FROM schedule "
            "WHERE weekday=? AND group_name=? ORDER BY lesson_num",
            (day, grp)).fetchall()
        if rows:
            mark = " 👈 сегодня" if day == today else ""
            out.append(f"{day}{mark}\n" + "\n".join(
                f"{r[0]}. {r[1]} (каб. {r[2] or '—'}, {r[3] or ''})" for r in rows))
    await event.message.answer(f"📅 Расписание группы {grp}\n\n" + ("\n\n".join(out)
                               or "Расписание для вашей группы пока пустое."))
    await event.answer(notification="Готово")

@dp.message_callback(F.callback.payload == "hw")
@guard
async def show_hw(event: MessageCallback):
    uid = get_uid(event)
    grp = get_group(uid)
    if grp:
        rows = db.execute(
            "SELECT subject, task, due_date FROM homework WHERE done=0 AND group_name=?",
            (grp,)).fetchall()
    else:
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

# ============ КОМАНДЫ АДМИНА ============
@dp.message_created(Command("add_lesson"))
async def add_lesson(event: MessageCreated):
    if not is_admin(get_uid(event)):
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()[1:]
    if len(p) != 6 or p[0].capitalize() not in WEEKDAYS:
        return await event.message.answer(
            "Формат: /add_lesson Понедельник 1 Программирование 204 09:00 ПКС-21")
    db.execute("INSERT INTO schedule(weekday, lesson_num, subject, room, time, group_name) "
               "VALUES (?,?,?,?,?,?)",
               (p[0].capitalize(), int(p[1]), p[2], p[3], p[4], p[5]))
    db.commit()
    await event.message.answer(f"✅ Пара добавлена для группы {p[5]}.")

@dp.message_created(Command("add_hw"))
async def add_hw(event: MessageCreated):
    if not is_admin(get_uid(event)):
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split(maxsplit=4)[1:]
    if len(p) != 4:
        return await event.message.answer(
            "Формат: /add_hw Алгебра 2026-11-15 ПКС-21 Повторить формулы")
    db.execute("INSERT INTO homework(subject, due_date, group_name, task) VALUES (?,?,?,?)",
               (p[0], p[1], p[2], p[3]))
    db.commit()
    await event.message.answer("✅ Домашка добавлена.")

@dp.message_created(Command("grade"))
async def put_grade(event: MessageCreated):
    if not is_admin(get_uid(event)):
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()[1:]
    if len(p) != 3:
        return await event.message.answer("Формат: /grade <user_id> <Предмет> <Оценка>")
    db.execute("INSERT INTO grades(user_id, subject, grade, date) VALUES (?,?,?,?)",
               (int(p[0]), p[1], p[2], datetime.now().strftime("%Y-%m-%d")))
    db.commit()
    await event.message.answer("✅ Оценка поставлена.")

@dp.message_created(Command("grant"))
async def grant(event: MessageCreated):
    if not is_admin(get_uid(event)):
        return await event.message.answer("⛔ Только для админа.")
    p = event.message.body.text.split()[1:]
    if len(p) != 2 or p[1] not in ("student", "admin"):
        return await event.message.answer("Формат: /grant <user_id> <student|admin>")
    db.execute("INSERT OR REPLACE INTO users(user_id, username, role) VALUES (?,?,?)",
               (int(p[0]), "", p[1]))
    db.commit()
    await event.message.answer("✅ Роль выдана.")

@dp.message_created(Command("reset"))
async def reset(event: MessageCreated):
    if not is_admin(get_uid(event)):
        return await event.message.answer("⛔ Только для админа.")
    for table in ("schedule", "homework", "grades", "auth", "codes", "users"):
        db.execute(f"DELETE FROM {table}")
    db.commit()
    await event.message.answer("🗑 Все данные очищены (кроме структуры).")

# ============ FALLBACK ============
@dp.message_created()
async def fallback(event: MessageCreated):
    if event.message.body.text:
        if is_approved(get_uid(event)):
            await event.message.answer("Не понял 🤔 Нажмите /start, чтобы открыть меню.")
        else:
            await event.message.answer("🔒 Для доступа отправьте: /login +7XXXXXXXXXX")

# ============ ЗАПУСК ============
async def main():
    try:
        await bot.delete_webhook()
        logger.info("Webhook-подписки сняты, запускаю polling")
    except Exception as e:
        logger.warning("Не удалось снять подписки: %s", e)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
