import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MSK = ZoneInfo("Europe/Moscow")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# (название, час_от, час_до)
WINDOWS = [
    ("morning",   6,  8),
    ("afternoon", 12, 14),
    ("evening",   20, 22),
]

TIME_HINTS = {
    "morning": (
        "первое утреннее — ОБЯЗАТЕЛЬНО начни с приветствия, каждый раз разного: "
        "Доброе утро! / С добрым утром! / Утро доброе! / Просыпайся, красавица! / "
        "Новый день уже ждёт! / Солнце встало — и ты вставай! и т.п. Бодрое, про хороший старт дня"
    ),
    "afternoon": "дневное — про середину дня, маленькие победы, перерыв и заботу о себе",
    "evening": (
        "последнее вечернее — про отдых и тепло, ОБЯЗАТЕЛЬНО заверши прощанием, каждый раз разным: "
        "Спокойной ночи! / До завтра! / Сладких снов! / Отдыхай, ты это заслужила! / "
        "Пусть ночь будет доброй! / Засыпай с улыбкой! и т.п."
    ),
}

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"

SYSTEM_PROMPT = """Ты — добрый и остроумный бот, который отправляет мотивирующие сообщения.

Твоя задача: написать одно мотивирующее сообщение (3–5 предложений) на одну из тем:
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
3. Длина: 3–5 предложений.
4. Используй 2–4 эмодзи по тексту и в конце — они делают сообщение теплее и живее. Подбирай эмодзи по настроению: 🌸💧✨🌿💛🌺🍵☕💪🌟💎🧘‍♀️🌻❤️😊😄🌙🌞🍰👑💄🌷💖 и другие подходящие.
5. Каждое сообщение должно быть уникальным — не повторяй уже сказанное.
6. Пиши только само сообщение, без вступлений и пояснений.

Примеры хорошего тона:
- "Доброе утро! ☀️ Ты сегодня снова в главной роли. Весь мир — твоя сцена. 🌸"
- "Женщина, которая умеет смеяться над собой, — непобедима 😄 И обаятельна до безобразия."
- "Здоровье — не наказание, а подарок 💎 Прими с благодарностью и выпей стакан воды. 💧"
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


async def generate_message(slot: str = "afternoon") -> str:
    hint = TIME_HINTS.get(slot, "мотивирующее")
    response = await anthropic_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=400,
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


async def send_motivation(slot: str = "afternoon") -> None:
    if not subscribers:
        return

    now = datetime.now(tz=MSK)
    slot_key = f"{now.strftime('%Y-%m-%d')}_{slot}"

    if slot_key in _sent_slots:
        return

    try:
        text = await generate_message(slot)
    except Exception as e:
        log.error("Ошибка генерации [%s]: %s", slot, e)
        return

    for chat_id in list(subscribers):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            log.warning("Не удалось отправить %s: %s", chat_id, e)

    _sent_slots.add(slot_key)
    log.info("Отправлено [%s]: %s", slot_key, text[:60])


def _schedule_day(date=None):
    """Планирует 3 случайных сообщения на указанный день."""
    now = datetime.now(tz=MSK)
    target = date or now.date()

    for slot_name, hour_from, hour_to in WINDOWS:
        job_id = f"motivation_{target}_{slot_name}"

        start_dt = datetime(target.year, target.month, target.day, hour_from, 0, tzinfo=MSK)
        end_dt = datetime(target.year, target.month, target.day, hour_to, 0, tzinfo=MSK)
        delta = int((end_dt - start_dt).total_seconds())
        fire_time = start_dt + timedelta(seconds=random.randint(0, delta - 1))

        if fire_time <= now:
            log.info("Пропускаю прошедший слот [%s %s]", target, slot_name)
            continue

        scheduler.add_job(
            send_motivation,
            DateTrigger(run_date=fire_time),
            kwargs={"slot": slot_name},
            id=job_id,
            replace_existing=True,
        )
        log.info("Запланировано [%s] на %s МСК", slot_name, fire_time.strftime("%H:%M"))


async def _reschedule_tomorrow():
    """Каждую ночь в 00:01 планирует сообщения на следующий день."""
    now = datetime.now(tz=MSK)
    tomorrow = (now + timedelta(days=1)).date()
    _schedule_day(tomorrow)
    log.info("Расписание на %s создано", tomorrow)


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    subscribers.add(message.chat.id)
    save_subscribers(subscribers)
    await message.answer(
        "Привет! 🌸 Теперь ты будешь получать мотивирующие сообщения 3 раза в день:\n"
        "☀️ Утром (6:00–8:00)\n"
        "🌤 Днём (12:00–14:00)\n"
        "🌙 Вечером (20:00–22:00)\n\n"
        "Чтобы отписаться — /stop"
    )
    try:
        text = await generate_message("morning")
        await message.answer(text)
    except Exception as e:
        log.error("Ошибка приветственного сообщения: %s", e)
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
        text = await generate_message("afternoon")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        return
    await message.answer(text)


@dp.message(Command("next"))
async def cmd_next(message: Message) -> None:
    now = datetime.now(tz=MSK)
    jobs = sorted(
        [j for j in scheduler.get_jobs() if j.id.startswith("motivation_")],
        key=lambda j: j.next_run_time,
    )
    upcoming = [j for j in jobs if j.next_run_time and j.next_run_time > now]
    if upcoming:
        t = upcoming[0].next_run_time.astimezone(MSK)
        await message.answer(f"Следующее сообщение в {t.strftime('%H:%M')} МСК.")
    else:
        await message.answer("На сегодня все сообщения отправлены. Ждём завтра! 🌙")


async def main() -> None:
    # Планируем сообщения на сегодня
    _schedule_day()

    # Каждую ночь в 00:01 планируем следующий день
    scheduler.add_job(
        _reschedule_tomorrow,
        CronTrigger(hour=0, minute=1, timezone=MSK),
        id="reschedule_daily",
    )

    scheduler.start()
    log.info("Планировщик запущен")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
