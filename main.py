import asyncio
import os
from datetime import date, timedelta

from aiomax import Bot
from aiomax.filters import F
from mosdiary import MOSDiaryClient

# Сначала — сертификаты, до любых сетевых запросов
from certs_helper import ensure_mincifry_certs
ensure_mincifry_certs()

BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)

# В реальном проекте используйте БД или зашифрованное хранилище.
# Для теста — словарь в памяти (токены теряются при рестарте).
user_tokens: dict[int, dict] = {}


@bot.on_message(F.command("start"))
async def cmd_start(update):
    await bot.send_message(
        chat_id=update.chat_id,
        text=(
            "Привет! Я бот для просмотра расписания МЭШ.\n\n"
            "🔐 Пароль я не запрашиваю. Вход — по QR-коду в приложении «Дневник МЭШ».\n\n"
            "Команды:\n"
            "/login — войти в МЭШ\n"
            "/schedule — показать расписание на сегодня и завтра"
        ),
    )


@bot.on_message(F.command("login"))
async def cmd_login(update):
    user_id = update.user_id
    chat_id = update.chat_id

    await bot.send_message(chat_id=chat_id, text="Генерирую QR-код для входа…")

    try:
        async with MOSDiaryClient() as diary:
            # return_type="qr" возвращает PNG-байты QR-кода
            qr_png, expires = await diary.start_qr_login(return_type="qr")

            # Загружаем изображение в MAX и отправляем пользователю
            photo = await bot.upload_image(qr_png)
            await bot.send_message(
                chat_id=chat_id,
                text="Отсканируйте QR-код в приложении «Дневник МЭШ» и подтвердите вход.",
                attachments=photo,
            )

            # Ждём, пока пользователь отсканирует код
            aupd_token, refresh_token = await diary.wait_qr_login(expires)

            user_tokens[user_id] = {
                "aupd_token": aupd_token,
                "refresh_token": refresh_token,
            }

            await bot.send_message(
                chat_id=chat_id,
                text="✅ Вход выполнен. Теперь отправьте /schedule.",
            )

    except Exception as e:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Не удалось войти. Попробуйте ещё раз позже.\n\n({type(e).__name__})",
        )


@bot.on_message(F.command("schedule"))
async def cmd_schedule(update):
    user_id = update.user_id
    chat_id = update.chat_id

    tokens = user_tokens.get(user_id)
    if not tokens:
        await bot.send_message(
            chat_id=chat_id,
            text="Сначала войдите в систему командой /login.",
        )
        return

    try:
        async with MOSDiaryClient(aupd_token=tokens["aupd_token"]) as diary:
            today = date.today()
            events = await diary.get_schedule_events(today, today + timedelta(days=1))

            if not events:
                await bot.send_message(
                    chat_id=chat_id,
                    text="На сегодня и завтра уроков нет.",
                )
                return

            lines = ["📅 Расписание на сегодня и завтра:\n"]
            for ev in events:
                lines.append(
                    f"{ev.date:%d.%m} | {ev.start_time}–{ev.end_time} | "
                    f"{ev.subject_name} | каб. {ev.room}"
                )

            await bot.send_message(chat_id=chat_id, text="\n".join(lines))

    except Exception as e:
        # Если токен истёк или сессия недействительна — просим войти заново
        user_tokens.pop(user_id, None)
        await bot.send_message(
            chat_id=chat_id,
            text="Не удалось получить расписание. Возможно, сессия истекла. "
                 "Попробуйте /login снова.",
        )


async def main():
    await bot.start()
    await bot.start_polling()


if __name__ == "__main__":
    asyncio.run(main())
