import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MSK = ZoneInfo("Europe/Moscow")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEND_HOURS = [8, 10, 12, 14, 16, 18, 20]

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"

SYSTEM_PROMPT = """Ты — добрый и остроумный бот, который отправляет мотивирующие сообщения.

Твоя задача: написать одно короткое мотивирующее сообщение (2–4 предложения) на одну из тем:
- женственность и красота
- любовь к себе и самоценность
- здоровье и забота о себе
- спорт и движение: мягкие напоминания позаниматься, подвигаться, сделать зарядку или прогуляться
- питание и вода: не переедать, есть осознанно, пить достаточно воды в течение дня
- юмор и жизнерадостность
- маленькие радости жизни
- внутренняя сила и уверенность

Правила:
1. Иногда (не всегда) обращайся к читателю ласково: дорогая, солнышко, милая.
2. Тон: тёплый, с мягким юмором, без пафоса и нравоучений.
3. Длина: 2–4 предложения, не больше.
4. В конце — один эмодзи, подходящий по настроению.
5. Каждое сообщение должно быть уникальным — не повторяй уже сказанное.
6. Пиши только само сообщение, без вступлений и пояснений.

Примеры хорошего тона:
- "Ты сегодня снова в главной роли. Весь мир — твоя сцена. 🌸"
- "Женщина, которая умеет смеяться над собой, — непобедима. И обаятельна до безобразия. 😄"
- "Здоровье — не наказание, а подарок. Прими с благодарностью и выпей стакан воды. 💧"
"""

anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=MSK)

_sent_slots: set[str] = set()


def load_subscribers() -> set[int]:
    if SUBSCRIBERS_FILE.exists():
        return set(json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")))
    return set()


def save_subscribers(subs: set[int]) -> None:
    SUBSCRIBERS_FILE.write_text(json.dumps(list(subs)), encoding="utf-8")


subscribers: set[int] = load_subscribers()


async def generate_message() -> str:
    now = datetime.now(tz=MSK)
    hour = now.hour
    time_hint = {
        8:  "утреннее — бодрое, про хороший старт дня",
        10: "утреннее — про кофе, энергию, планы",
        12: "дневное — про середину дня, маленькие победы",
        14: "дневное — про обед, перерыв, заботу о себе",
        16: "послеобеденное — про второй ветер, настроение",
        18: "вечернее — про итоги дня, гордость собой",
        20: "вечернее — про отдых, тепло, хорошие мысли перед сном",
    }
    hint = time_hint.get(hour, "мотивирующее")

    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Напиши {hint} сообщение.",
            }
        ],
    )
    return response.content[0].text.strip()


async def send_motivation(hour: int | None = None) -> None:
    if not subscribers:
        return

    now = datetime.now(tz=MSK)
    h = hour if hour is not None else now.hour
    slot_key = f"{now.strftime('%Y-%m-%d')}_{h:02d}"

    if slot_key in _sent_slots:
        return

    try:
        text = await generate_message()
    except Exception as e:
        log.error("Ошибка генерации сообщения: %s", e)
        return

    for chat_id in list(subscribers):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            log.warning("Не удалось отправить %s: %s", chat_id, e)

    _sent_slots.add(slot_key)
    log.info("Отправлено [%s] %d подписчикам: %s", slot_key, len(subscribers), text[:60])


async def _check_missed() -> None:
    now = datetime.now(tz=MSK)
    if now.hour in SEND_HOURS and now.minute < 30:
        log.info("Догоняю пропущенный слот %d:00", now.hour)
        await send_motivation(now.hour)


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    subscribers.add(message.chat.id)
    save_subscribers(subscribers)
    await message.answer(
        "Привет! 🌸 Теперь ты будешь получать мотивирующие сообщения каждые 2 часа с 8:00 до 20:00 МСК.\n\n"
        "Чтобы отписаться — /stop"
    )
    log.info("Новый подписчик: %s", message.chat.id)


@dp.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    subscribers.discard(message.chat.id)
    save_subscribers(subscribers)
    await message.answer("Ты отписалась. Чтобы подписаться снова — /start 💛")
    log.info("Отписался: %s", message.chat.id)


@dp.message(Command("test"))
async def cmd_test(message: Message) -> None:
    try:
        text = await generate_message()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return
    await message.answer(text)


@dp.message(Command("next"))
async def cmd_next(message: Message) -> None:
    now = datetime.now(tz=MSK)
    upcoming = [h for h in SEND_HOURS if h > now.hour]
    if upcoming:
        await message.answer(f"Следующее сообщение в {upcoming[0]}:00 МСК.")
    else:
        await message.answer("На сегодня все сообщения отправлены. Завтра в 8:00 МСК.")


async def main() -> None:
    for hour in SEND_HOURS:
        scheduler.add_job(
            send_motivation,
            CronTrigger(hour=hour, minute=0, timezone=MSK),
            kwargs={"hour": hour},
            misfire_grace_time=3600,
            coalesce=True,
            id=f"motivation_{hour:02d}",
        )

    scheduler.start()
    log.info("Планировщик запущен. Часы отправки: %s МСК", SEND_HOURS)
    await _check_missed()
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
