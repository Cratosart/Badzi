import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton
)
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

bot = Bot(BOT_TOKEN,parse_mode="HTML")
dp = Dispatcher()
r = Router()
dp.include_router(r)

# -------- Тексты --------
INTRO_BTN = "Хочу на консультацию Ба-Цзы"

BTN_FORMAT = "❓ Формат и стоимость"
BTN_SIGNUP = "✍️ Записаться на консультацию"
BTN_BACK = "⬅️ Назад"
BTN_MENU = "🏠 В меню"
BTN_CANCEL = "❌ Отмена"

INTRO_TEXT = (
    "Консультация Ба-Цзы — это разбор характера человека и его судьбы по дате и времени рождения на основе "
    "китайской метафизики <tg-emoji emoji-id=\"6008095274948366144\">☯️</tg-emoji>\n\nРазбор — это встреча со своим внутренним я, с сильными и слабыми качествами личности; "
    "это ясный взгляд на здоровье, семью, финансы, профессию, совместимость и другие аспекты вашей жизни."
)

FORMAT_TEXT = (
    "Формат консультации\n\n"
    "🪷 живая встреча в Москве/Петербурге или онлайн созвон\n"
    "🪷 длительность консультации 2 часа\n"
    "🪷 полный разбор вашей карты ба-цзы\n"
    "🪷 стоимость 26 000₽"
)

ASK_BIRTH = (
    "Введите ваши данные рождения: дату, время и город\n"
    "<i>(если не знаете время рождения — не беда, проводится восстановление времени рождения).</i>"
)
ASK_QUERY = "Расскажите, какой запрос привёл вас на консультацию?"
THANKS = "Благодарю 🙌 Я скоро свяжусь с вами!"

# -------- FSM --------
class Signup(StatesGroup):
    waiting_birth = State()
    waiting_query = State()

# -------- Модель заявки --------
@dataclass
class Lead:
    user_id: int
    username: Optional[str]
    full_name: str
    started_at: datetime
    birth_data: Optional[str] = None
    query_text: Optional[str] = None
    completed: bool = False

pending_leads: dict[int, Lead] = {}

# -------- Клавиатуры (Reply) --------
def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=INTRO_BTN)]],
        resize_keyboard=True
    )

def kb_intro() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FORMAT)],
            [KeyboardButton(text=BTN_SIGNUP)],
            [KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True
    )

def kb_format() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BACK)],
            [KeyboardButton(text=BTN_SIGNUP)],
            [KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True
    )

def kb_cancel_to_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CANCEL)],
            [KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True
    )

# -------- Уведомления админу --------
async def safe_send_admin(text: str):
    if not ADMIN_CHAT_ID:
        return
    try:
        await bot.send_message(ADMIN_CHAT_ID, text)
    except Exception as e:
        logging.warning(f"Admin notify failed: {e!r}")

async def notify_admin_new_attempt(lead: Lead):
    text = (
        "🟡 Новая попытка записи (пока незавершена)\n\n"
        f"Пользователь: {lead.full_name} (@{lead.username or '—'})\n"
        f"User ID: {lead.user_id}\n"
        f"Начато: {lead.started_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    await safe_send_admin(text)

async def notify_admin_abandoned(lead: Lead):
    text = (
        "🟥 Попытка записи НЕ завершена (тайм-аут)\n\n"
        f"Пользователь: {lead.full_name} (@{lead.username or '—'})\n"
        f"User ID: {lead.user_id}\n"
        f"Начато: {lead.started_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"Введено:\n"
        f"— Данные рождения: {lead.birth_data or '—'}\n"
        f"— Запрос: {lead.query_text or '—'}"
    )
    await safe_send_admin(text)

async def notify_admin_completed(lead: Lead):
    text = (
        "🟢 Новая запись на консультацию\n\n"
        f"Пользователь: {lead.full_name} (@{lead.username or '—'})\n"
        f"User ID: {lead.user_id}\n"
        f"Заявка:\n"
        f"— Данные рождения: {lead.birth_data}\n"
        f"— Запрос: {lead.query_text}\n"
    )
    await safe_send_admin(text)

# -------- Тайм-аут незавершённых заявок --------
ABANDON_TIMEOUT = timedelta(minutes=30)

async def schedule_abandon_check(user_id: int):
    await asyncio.sleep(ABANDON_TIMEOUT.total_seconds())
    lead = pending_leads.get(user_id)
    if lead and not lead.completed:
        await notify_admin_abandoned(lead)
        pending_leads.pop(user_id, None)

# -------- Команды --------
@r.message(Command("test_emoji"))
async def test_emoji(message: Message):
    await message.answer('<tg-emoji emoji-id="6008095274948366144">☯️</tg-emoji>')


@r.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=kb_main())

@r.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Ваш chat_id: {message.chat.id}")

# -------- Навигация кнопками (Reply) --------
@r.message(F.text == INTRO_BTN)
async def on_intro(message: Message):
    await message.answer(INTRO_TEXT, reply_markup=kb_intro())

@r.message(F.text == BTN_FORMAT)
async def on_format(message: Message):
    await message.answer(FORMAT_TEXT, reply_markup=kb_format())

@r.message(F.text == BTN_BACK)
async def on_back(message: Message):
    # Назад из «Формат и стоимость» к интро-экрану
    await message.answer(INTRO_TEXT, reply_markup=kb_intro())

@r.message(F.text == BTN_MENU)
async def on_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=kb_main())

# -------- Запись (Reply) --------
@r.message(F.text == BTN_SIGNUP)
async def on_signup(message: Message, state: FSMContext):
    user = message.from_user
    lead = Lead(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        started_at=datetime.now(timezone.utc),
    )
    pending_leads[user.id] = lead
    asyncio.create_task(schedule_abandon_check(user.id))
    await notify_admin_new_attempt(lead)

    await state.set_state(Signup.waiting_birth)
    await message.answer(ASK_BIRTH, reply_markup=kb_cancel_to_menu())

@r.message(Signup.waiting_birth, F.text == BTN_CANCEL)
@r.message(Signup.waiting_query, F.text == BTN_CANCEL)
async def on_cancel(message: Message, state: FSMContext):
    # Отмена и возврат в меню
    user_id = message.from_user.id
    pending_leads.pop(user_id, None)
    await state.clear()
    await message.answer("Отменено. Вы в главном меню.", reply_markup=kb_main())

@r.message(Signup.waiting_birth, F.text)
async def on_birth_data(message: Message, state: FSMContext):
    user_id = message.from_user.id
    txt = message.text.strip()
    lead = pending_leads.get(user_id)
    if not lead:
        lead = Lead(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            started_at=datetime.now(timezone.utc),
        )
        pending_leads[user_id] = lead
    lead.birth_data = txt
    await state.set_state(Signup.waiting_query)
    await message.answer(ASK_QUERY, reply_markup=kb_cancel_to_menu())

@r.message(Signup.waiting_query, F.text)
async def on_query(message: Message, state: FSMContext):
    user_id = message.from_user.id
    lead = pending_leads.get(user_id)
    if not lead:
        lead = Lead(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            started_at=datetime.now(timezone.utc),
        )
        pending_leads[user_id] = lead

    lead.query_text = message.text.strip()
    lead.completed = True

    await notify_admin_completed(lead)
    await message.answer(THANKS, reply_markup=kb_main())

    await state.clear()
    pending_leads.pop(user_id, None)

# Подстраховка: если прилетит не-текст во время ввода
@r.message(Signup.waiting_birth)
async def on_birth_unknown(message: Message):
    await message.answer("Пожалуйста, отправьте текстом дату, время и город рождения или нажмите «Отмена».", reply_markup=kb_cancel_to_menu())

@r.message(Signup.waiting_query)
async def on_query_unknown(message: Message):
    await message.answer("Пожалуйста, опишите ваш запрос текстом или нажмите «Отмена».", reply_markup=kb_cancel_to_menu())

# -------- Точка входа --------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Укажите его в .env")
    if not ADMIN_CHAT_ID:
        logging.warning("ADMIN_CHAT_ID не задан — администратор не будет получать уведомления.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
