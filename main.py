import asyncio
import sqlite3
import os
import pandas as pd
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- НАСТРОЙКА БОТА ---
TOKEN = "ТВОЙ_ТОКЕН_СЮДА"
ADMIN_ID = 481597187  # Твой Telegram ID для автоматических отчетов (при необходимости)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            client_name TEXT,
            material TEXT,
            start_weight REAL,
            start_stones TEXT,
            start_photo_id TEXT DEFAULT NULL,
            end_weight REAL DEFAULT NULL,
            end_stones_weight REAL DEFAULT 0.0,
            end_stones TEXT DEFAULT NULL,
            end_photo_id TEXT DEFAULT NULL,
            price REAL DEFAULT NULL,
            status TEXT DEFAULT 'В работе'
        )
    ''')
    conn.commit()
    conn.close()

# --- ФУНКЦИЯ ДЛЯ EXCEL (С НОВЫМИ РАСЧЕТАМИ) ---
def get_excel_report(user_id=None, all_orders=False):
    conn = sqlite3.connect('jewelry_orders.db')
    if all_orders:
        df = pd.read_sql_query("SELECT * FROM orders", conn)
    else:
        df = pd.read_sql_query("SELECT * FROM orders WHERE telegram_id = ?", conn, params=(user_id,))
    conn.close()
    
    if df.empty:
        return None
        
    # Расчет по новой формуле
    df['Потери металла, г'] = ((df['end_weight'] * 1.09) - df['start_weight']).round(3)
    df['Потери + Чистый вес, г'] = (df['Потери металла, г'] + df['end_weight']).round(3)
    
    df_beauty = df.rename(columns={
        'id': 'ID Заказа',
        'client_name': 'Клиент',
        'material': 'Материал / Проба',
        'start_weight': 'Входной вес (г)',
        'start_stones': 'Планируемые камни',
        'end_weight': 'Чистый вес металла (г)',
        'end_stones_weight': 'Вес камней (г)',
        'end_stones': 'Фактические камни',
        'price': 'Стоимость (руб)',
        'status': 'Статус заказа'
    })
    
    df_beauty = df_beauty.drop(columns=['telegram_id', 'start_photo_id', 'end_photo_id'], errors='ignore')
    return df_beauty

# --- СОСТОЯНИЯ (FSM) ---
class NewOrder(StatesGroup):
    client_name = State()
    material = State()
    start_weight = State()
    start_stones = State()
    photo = State()

class CloseOrder(StatesGroup):
    order_id = State()
    end_weight = State()
    end_stones_weight = State()
    end_stones = State()
    price = State()
    end_photo = State()

class EditOrder(StatesGroup):
    order_id = State()
    waiting_new_value = State()

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Новый заказ"), KeyboardButton(text="🏁 Завершить заказ")],
        [KeyboardButton(text="✏️ Редактировать"), KeyboardButton(text="📋 Активные заказы")],
        [KeyboardButton(text="📊 Отчет в Excel")]
    ],
    resize_keyboard=True
)

skip_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="⏩ Пропустить фото")]],
    resize_keyboard=True
)

# --- ХЕНДЛЕРЫ ---

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer("Привет! Я бот-помощник для личного учета ювелирных заказов.", reply_markup=main_kb)

# --- ВЫГРУЗКА В EXCEL ---
@dp.message(F.text == "📊 Отчет в Excel")
async def export_to_excel(message: Message):
    await message.answer("🔄 Формирую ваш личный отчет, подождите немного...")
    
    df_beauty = get_excel_report(user_id=message.from_user.id)
    if df_beauty is None:
        await message.answer("Ваша база данных пока пуста. Нечего выгружать.")
        return
        
    filename = f"Ювелирные_Заказы_{message.from_user.id}.xlsx"
    df_beauty.to_excel(filename, index=False)
    
    excel_file = FSInputFile(filename)
    await message.answer_document(document=excel_file, caption="📋 Вот твой свежий отчет по твоим заказам!")
    if os.path.exists(filename):
        os.remove(filename)

# --- СЦЕНАРИЙ: НОВЫЙ ЗАКАЗ ---
@dp.message(F.text == "➕ Новый заказ")
async def start_new_order(message: Message, state: FSMContext):
    await state.set_state(NewOrder.client_name)
    await message.answer("Введите имя или контакты клиента:")

@dp.message(NewOrder.client_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await state.set_state(NewOrder.material)
    await message.answer("Из какого материала заказ? (Например: Золото 585, Серебро 925):")

@dp.message(NewOrder.material)
async def process_material(message: Message, state: FSMContext):
    await state.update_data(material=message.text)
    await state.set_state(NewOrder.start_weight)
    await message.answer("Внесите входной вес металла (в граммах):")

@dp.message(NewOrder.start_weight)
async def process_start_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(start_weight=weight)
        await state.set_state(NewOrder.start_stones)
        await message.answer("Какие камни планируются? (Если нет, напишите 'Нет'):")
    except ValueError:
        await message.answer("Пожалуйста, введите вес числом:")

@dp.message(NewOrder.start_stones)
async def process_start_stones(message: Message, state: FSMContext):
    await state.update_data(start_stones=message.text)
    await state.set_state(NewOrder.photo)
    await message.answer("Отправьте фото изделия/эскиза или нажмите кнопку ниже, чтобы пропустить:", reply_markup=skip_kb)

@dp.message(NewOrder.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await save_order_to_db(photo_id, message, state)

@dp.message(NewOrder.photo, F.text == "⏩ Пропустить фото")
async def process_skip_photo(message: Message, state: FSMContext):
    await save_order_to_db(None, message, state)

async def save_order_to_db(photo_id, message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (telegram_id, client_name, material, start_weight, start_stones, start_photo_id)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (message.from_user.id, data['client_name'], data['material'], data['start_weight'], data['start_stones'], photo_id))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    caption = (
        f"✅ Заказ успешно создан!\n"
        f"🆔 <b>ID заказа: {order_id}</b>\n"
        f"👤 Клиент: {data['client_name']}\n"
        f"📦 Материал: {data['material']}\n"
        f"⚖️ Входной вес: {data['start_weight']} г\n"
        f"💎 Камни: {data['start_stones']}"
    )
    if photo_id:
        await message.answer_photo(photo=photo_id, caption=caption, parse_mode="HTML", reply_markup=main_kb)
    else:
        await message.answer(caption, parse_mode="HTML", reply_markup=main_kb)
    await state.clear()

# --- СЦЕНАРИЙ: ЗАВЕРШЕНИЕ ЗАКАЗА ---
@dp.message(F.text == "🏁 Завершить заказ")
async def start_close_order(message: Message, state: FSMContext):
    await state.set_state(CloseOrder.order_id)
    await message.answer("Введите ID заказа, который нужно завершить:")

@dp.message(CloseOrder.order_id)
async def process_close_id(message: Message, state: FSMContext):
    try:
        order_id = int(message.text)
        conn = sqlite3.connect('jewelry_orders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT client_name, material, start_weight, start_photo_id FROM orders WHERE id = ? AND telegram_id = ? AND status = 'В работе'", (order_id, message.from_user.id))
        order = cursor.fetchone()
        conn.close()
        
        if order:
            await state.update_data(order_id=order_id, start_weight=order[2])
            await state.set_state(CloseOrder.end_weight)
            await message.answer(f"Заказ найден (Клиент: {order[0]}).\nВведите готовый вес ТОЛЬКО металла (в граммах):")
        else:
            await message.answer("Заказ с таким ID не найден в вашем списке или уже завершен.")
            await state.clear()
    except ValueError:
        await message.answer("ID должен быть числом:")

@dp.message(CloseOrder.end_weight)
async def process_end_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(end_weight=weight)
        await state.set_state(CloseOrder.end_stones_weight)
        await message.answer("Введите общий вес закрепленных камней (в граммах, если камней нет — введите 0):")
    except ValueError:
        await message.answer("Введите вес числом:")

@dp.message(CloseOrder.end_stones_weight)
async def process_end_stones_weight(message: Message, state: FSMContext):
    try:
        stones_weight = float(message.text.replace(',', '.'))
        await state.update_data(end_stones_weight=stones_weight)
        await state.set_state(CloseOrder.end_stones)
        await message.answer("Какие камни фактически закрепили? (Наименование/количество, если нет — напишите 'Нет'):")
    except ValueError:
        await message.answer("Введите вес камней числом:")

@dp.message(CloseOrder.end_stones)
async def process_end_stones(message: Message, state: FSMContext):
    await state.update_data(end_stones=message.text)
    await state.set_state(CloseOrder.price)
    await message.answer("Введите итоговую стоимость заказа:")

@dp.message(CloseOrder.price)
async def process_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(',', '.').replace(' ', ''))
        await state.update_data(price=price)
        await state.set_state(CloseOrder.end_photo)
        await message.answer("Отправьте фото готового изделия, либо нажмите кнопку ниже, чтобы пропустить:", reply_markup=skip_kb)
    except ValueError:
        await message.answer("Введите сумму числом:")

@dp.message(CloseOrder.end_photo, F.photo)
async def process_end_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await finalize_order(photo_id, message, state)

@dp.message(CloseOrder.end_photo, F.text == "⏩ Пропустить фото")
async def process_skip_end_photo(message: Message, state: FSMContext):
    await finalize_order(None, message, state)

async def finalize_order(end_photo_id, message: Message, state: FSMContext):
    data = await state.get_data()
    
    # Расчет по новым правилам:
    # 1. Потери металла = (чистый готовый вес металла * 1.09) - изначальный вес металла
    loss = round((data['end_weight'] * 1.09) - data['start_weight'], 3)
    # 2. Потери + чистый вес готового металла
    total_metal_balance = round(loss + data['end_weight'], 3)
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE orders 
        SET end_weight = ?, end_stones_weight = ?, end_stones = ?, price = ?, end_photo_id = ?, status = 'Завершен'
        WHERE id = ? AND telegram_id = ?
    ''', (data['end_weight'], data['end_stones_weight'], data['end_stones'], data['price'], end_photo_id, data['order_id'], message.from_user.id))
    conn.commit()
    conn.close()
    
    caption = (
        f"🎉 Заказ №{data['order_id']} успешно закрыт!\n\n"
        f"⚖️ Входной вес металла: {data['start_weight']} г\n"
        f"⚖️ Чистый вес готового металла: {data['end_weight']} г\n"
        f"💎 Вес закрепленных камней: {data['end_stones_weight']} г ({data['end_stones']})\n"
        f"📉 Потери (металл +9%): {loss} г\n"
        f"📊 Итог (Потери + Чистый вес): {total_metal_balance} г\n"
        f"💰 Сумма: {data['price']} руб."
    )
    if end_photo_id:
        await message.answer_photo(photo=end_photo_id, caption=caption, reply_markup=main_kb)
    else:
        await message.answer(caption, reply_markup=main_kb)
    await state.clear()

# --- ПРОСМОТР АКТИВНЫХ ---
@dp.message(F.text == "📋 Активные заказы")
async def show_active_orders(message: Message):
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_name, material, start_weight FROM orders WHERE status = 'В работе' AND telegram_id = ?", (message.from_user.id,))
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        await message.answer("У вас сейчас нет активных заказов в работе.")
        return
        
    response = "<b>Ваши заказы в работе:</b>\n\n"
    for o in orders:
        response += f"🆔 ID: {o[0]} | 👤 {o[1]} | 📦 {o[2]} | ⚖️ {o[3]}г\n"
    
    await message.answer(response, parse_mode="HTML")

# --- ЗАПУСК БОТА ---
async def main():
    init_db()
    print("Бот запущен с учетом веса камней и новой формулой баланса металла!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
