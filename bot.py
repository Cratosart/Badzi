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
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from dotenv import load_dotenv

# -------------------- Настройка --------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
r = Router()
dp.include_router(r)

# -------------------- Тексты --------------------
INTRO_BTN = "Хочу на консультацию Ба-Цзы"
INTRO_TEXT = (
    "Консультация Ба-Цзы — это разбор характера человека и его судьбы по дате и времени рождения на основе "
    "китайской метафизики ☯️ Разбор — это встреча со своим внутренним я, с сильными и слабыми качествами личности; "
    "это ясный взгляд на здоровье, семью, финансы, профессию, совместимость и другие аспекты вашей жизни."
)

FORMAT_TEXT = (
    "Формат консультации:\n\n"
    "🪷 живая встреча в Москве/Петербурге или онлайн созвон\n"
    "🪷 длительность консультации 2 часа\n"
    "🪷 полный разбор вашей карты ба-цзы\n"
    "🪷 стоимость 26 000₽"
)

ASK_BIRTH = (
    "Введите ваши данные рождения: дату, время и город\n"
    "(если не знаете время рождения — не беда, для консультации проводится восстановление времени рождения)."
)
ASK_QUERY = "Расскажите, какой запрос привёл вас на консультацию?"
THANKS = "Благодарю 🙌 Я скоро свяжусь с вами!"

# -------------------- FSM состояния --------------------
class Signup(StatesGroup):
    waiting_birth = State()
    waiting_query = State()

# -------------------- Модель заявки --------------------
@dataclass
class Lead:
    user_id: int
    username: Optional[str]
    full_name: str
    started_at: datetime
    birth_data: Optional[str] = None
    query_text: Optional[str] = None
    completed: bool = False

# Пул незавершённых заявок в памяти
pending_leads: dict[int, Lead] = {}

# -------------------- Клавиатуры --------------------
def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=INTRO_BTN)]],
        resize_keyboard=True
    )

def intro_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="• Формат и стоимость", callback_data="format")],
        [InlineKeyboardButton(text="• Записаться на консультацию", callback_data="signup")]
    ])

def format_inline_kb() -> InlineKeyboardMarkup:
    # Кнопка «Назад» возвращает на стартовый экран (intro)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_intro")]
    ])

def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_menu")]
    ])

# -------------------- Уведомления админу --------------------
async def notify_admin_new_attempt(lead: Lead):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "🟡 Новая попытка записи (пока незавершена)\n\n"
        f"Пользователь: {lead.full_name} (@{lead.username or '—'})\n"
        f"User ID: {lead.user_id}\n"
        f"Начато: {lead.started_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    await bot.send_message(ADMIN_CHAT_ID, text)

async def notify_admin_abandoned(lead: Lead):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "🟥 Попытка записи НЕ завершена (тайм-аут)\n\n"
        f"Пользователь: {lead.full_name} (@{lead.username or '—'})\n"
        f"User ID: {lead.user_id}\n"
        f"Начато: {lead.started_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"Введено:\n"
        f"— Данные рождения: {lead.birth_data or '—'}\n"
        f"— Запрос: {lead.query_text or '—'}"
    )
    await bot.send_message(ADMIN_CHAT_ID, text)

async def notify_admin_completed(lead: Lead):
    if not ADMIN_CHAT_ID:
        return
    text = (
        "🟢 Новая запись на консультацию\n\n"
        f"Пользователь: {lead.full_name} (@{lead.username or '—'})\n"
        f"User ID: {lead.user_id}\n"
        f"Заявка:\n"
        f"— Данные рождения: {lead.birth_data}\n"
        f"— Запрос: {lead.query_text}\n"
    )
    await bot.send_message(ADMIN_CHAT_ID, text)

# -------------------- Тайм-аут незавершённых заявок --------------------
ABANDON_TIMEOUT = timedelta(minutes=30)

async def schedule_abandon_check(user_id: int):
    await asyncio.sleep(ABANDON_TIMEOUT.total_seconds())
    lead = pending_leads.get(user_id)
    if lead and not lead.completed:
        await notify_admin_abandoned(lead)
        pending_leads.pop(user_id, None)

# -------------------- Команды --------------------
@r.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=main_menu_kb())

@r.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Ваш chat_id: {message.chat.id}")

# -------------------- Основной сценарий --------------------
@r.message(F.text == INTRO_BTN)
async def show_intro(message: Message):
    await message.answer(INTRO_TEXT, reply_markup=intro_inline_kb())

@r.callback_query(F.data == "format")
async def on_format(call: CallbackQuery):
    # Экран «Формат и стоимость» + кнопка «Назад» в intro
    await call.message.edit_text(FORMAT_TEXT, reply_markup=format_inline_kb())
    await call.answer()

@r.callback_query(F.data == "back_intro")
async def on_back_intro(call: CallbackQuery):
    # Возврат к стартовому экрану (intro) с двумя кнопками
    await call.message.edit_text(INTRO_TEXT, reply_markup=intro_inline_kb())
    await call.answer()

@r.callback_query(F.data == "back_to_menu")
async def on_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Выберите действие:", reply_markup=None)
    await call.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await call.answer()

@r.callback_query(F.data == "signup")
async def on_signup(call: CallbackQuery, state: FSMContext):
    user = call.from_user
    lead = Lead(
        user_id=user.id,
        username=user.username,
        full_name=f"{user.full_name}",
        started_at=datetime.now(timezone.utc)
    )
    pending_leads[user.id] = lead

    # Сразу уведомим про попытку и запланируем проверку тайм-аута
    await notify_admin_new_attempt(lead)
    asyncio.create_task(schedule_abandon_check(user.id))

    await state.set_state(Signup.waiting_birth)
    await call.message.edit_text(ASK_BIRTH)
    await call.answer()

@r.message(Signup.waiting_birth, F.text)
async def on_birth_data(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in pending_leads:
        pending_leads[user_id] = Lead(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            started_at=datetime.now(timezone.utc),
            birth_data=message.text.strip()
        )
    else:
        pending_leads[user_id].birth_data = message.text.strip()

    await state.set_state(Signup.waiting_query)
    await message.answer(ASK_QUERY)

@r.message(Signup.waiting_query, F.text)
async def on_query(message: Message, state: FSMContext):
    user_id = message.from_user.id
    lead = pending_leads.get(user_id)
    if not lead:
        lead = Lead(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            started_at=datetime.now(timezone.utc)
        )
        pending_leads[user_id] = lead

    lead.query_text = message.text.strip()
    lead.completed = True

    await notify_admin_completed(lead)
    await message.answer(THANKS, reply_markup=main_menu_kb())

    await state.clear()
    pending_leads.pop(user_id, None)

# Подстраховка: не-текст в местах, где ждём текст
@r.message(Signup.waiting_birth)
async def on_birth_unknown(message: Message):
    await message.answer("Пожалуйста, отправьте текстом дату, время и город рождения.")

@r.message(Signup.waiting_query)
async def on_query_unknown(message: Message):
    await message.answer("Пожалуйста, опишите ваш запрос текстом.")

# -------------------- Точка входа --------------------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Укажите его в .env")
    if not ADMIN_CHAT_ID:
        logging.warning("ADMIN_CHAT_ID не задан — администратор не будет получать уведомления.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
