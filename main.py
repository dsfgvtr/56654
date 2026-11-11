import os
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI
from aiomax2 import Bot, Dispatcher, Router
from aiomax2.filters import F
from aiomax2.types import Message
from mosdiary import MOSDiaryClient

from certs_helper import ensure_mincifry_certs

# --- Сертификаты Минцифры: вызываем ДО любых сетевых запросов ---
ensure_mincifry_certs()

logging.basicConfig(level=logging.INFO)

# --- Переменные окружения (задаются в панели Bothost) ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")          # 5–256 символов A-Z a-z 0-9 _ -
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")   # локальный путь для FastAPI
PUBLIC_DOMAIN = os.getenv("BOTHOST_DOMAIN")            # домен из панели Bothost

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")
if not PUBLIC_DOMAIN:
    raise ValueError("Не задана переменная окружения BOTHOST_DOMAIN")

WEBHOOK_URL = f"https://{PUBLIC_DOMAIN}{WEBHOOK_PATH}"

# --- Инициализация aiomax2 ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# В реальном проекте используйте БД или зашифрованное хранилище.
# Для теста — словарь в памяти (токены теряются при рестарте).
user_tokens: dict[int, dict] = {}


# ========== ОБРАБОТЧИКИ ==========

@router.message(F.command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я бот для просмотра расписания МЭШ.\n\n"
        "🔐 Пароль я не запрашиваю. Вход — по QR-коду в приложении «Дневник МЭШ».\n\n"
        "Команды:\n"
        "/login — войти в МЭШ\n"
        "/schedule — показать расписание на сегодня и завтра"
    )


@router.message(F.command("login"))
async def cmd_login(message: Message) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id

    await message.answer("Генерирую QR-код для входа…")

    try:
        async with MOSDiaryClient() as diary:
            qr_png, expires = await diary.start_qr_login(return_type="qr")

            # Загрузка изображения и отправка (метод upload_image зависит от версии aiomax2)
            photo = await bot.upload_image(qr_png)
            await message.answer_photo(
                photo=photo,
                caption="Отсканируйте QR-код в приложении «Дневник МЭШ» и подтвердите вход."
            )

            aupd_token, refresh_token = await diary.wait_qr_login(expires)

            user_tokens[user_id] = {
                "aupd_token": aupd_token,
                "refresh_token": refresh_token,
            }

            await message.answer("✅ Вход выполнен. Теперь отправьте /schedule.")

    except Exception as e:
        await message.answer(
            f"❌ Не удалось войти. Попробуйте ещё раз позже.\n\n({type(e).__name__})"
        )


@router.message(F.command("schedule"))
async def cmd_schedule(message: Message) -> None:
    user_id = message.from_user.id
    chat_id = message.chat.id

    tokens = user_tokens.get(user_id)
    if not tokens:
        await message.answer("Сначала войдите в систему командой /login.")
        return

    try:
        async with MOSDiaryClient(aupd_token=tokens["aupd_token"]) as diary:
            today = date.today()
            events = await diary.get_schedule_events(today, today + timedelta(days=1))

            if not events:
                await message.answer("На сегодня и завтра уроков нет.")
                return

            lines = ["📅 Расписание на сегодня и завтра:\n"]
            for ev in events:
                lines.append(
                    f"{ev.date:%d.%m} | {ev.start_time}–{ev.end_time} | "
                    f"{ev.subject_name} | каб. {ev.room}"
                )

            await message.answer("\n".join(lines))

    except Exception:
        user_tokens.pop(user_id, None)
        await message.answer(
            "Не удалось получить расписание. Возможно, сессия истекла. "
            "Попробуйте /login снова."
        )


# ========== WEBHOOK + FASTAPI ==========

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Подписка на вебхук при старте. Используем resolve_used_update_types(),
    # чтобы подписаться только на те события, для которых есть обработчики.
    await bot.subscribe(
        WEBHOOK_URL,
        secret=WEBHOOK_SECRET,
        update_types=dp.resolve_used_update_types(),
    )
    logging.info(f"Вебхук подписан на {WEBHOOK_URL}")
    yield
    # Отписка и закрытие ресурсов при остановке
    await bot.unsubscribe()
    await bot.close()
    await dp.close()
    logging.info("Вебхук отписан, ресурсы закрыты")


app = FastAPI(lifespan=lifespan)

# Подключаем роутер aiomax2, который принимает POST-запросы от MAX,
# проверяет secret и передаёт обновления в Dispatcher через feed_update.
app.include_router(dp.webhook_router(WEBHOOK_PATH, bot=bot, secret=WEBHOOK_SECRET))
