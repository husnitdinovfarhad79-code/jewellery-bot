import asyncio
import sqlite3
import os
import pandas as pd
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- НАСТРОЙКА БОТА ---
TOKEN = "8731687908:AAF1K5UJjSUbY5Nwgv1ye4gTay36i130GMs" 
ADMIN_ID = 481597187  # Твой точный Telegram ID для получения отчетов раз в 3 дня
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
            end_stones TEXT DEFAULT NULL,
            end_photo_id TEXT DEFAULT NULL,
            price REAL DEFAULT NULL,
            status TEXT DEFAULT 'В работе'
        )
    ''')
    conn.commit()
    conn.close()

# --- МИКРО-СЕРВЕР ДЛЯ ОБМАНА RENDER ---
async def handle(request):
    return web.Response(text="Ювелирный бот успешно запущен и работает в сети!")

async def start_background_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- ФУНКЦИЯ ДЛЯ ПЕРЕРАСЧЕТА ПОТЕРЬ ПО НОВОЙ ФОРМУЛЕ (ДЛЯ EXCEL) ---
def build_full_excel_report():
    conn = sqlite3.connect('jewelry_orders.db')
    df = pd.read_sql_query("SELECT * FROM orders", conn)
    conn.close()
    
    if df.empty:
        return None
        
    # Формула: (готовый вес * 1.09) - изначальный вес
    df['Потери (+9%), г'] = (df['end_weight'] * 1.09) - df['start_weight']
    df['Потери (+9%), г'] = df['Потери (+9%), г'].round(3)
    
    df_beauty = df.rename(columns={
        'id': 'ID Заказа',
        'telegram_id': 'ID Мастера (ТГ)',
        'client_name': 'Клиент',
        'material': 'Материал / Проба',
        'start_weight': 'Входной вес (г)',
        'start_stones': 'Планируемые камни',
        'end_weight': 'Финальный вес (г)',
        'end_stones': 'Фактические камни',
        'price': 'Стоимость (руб)',
        'status': 'Статус заказа'
    })
    
    df_beauty = df_beauty.drop(columns=['start_photo_id', 'end_photo_id'], errors='ignore')
    
    filename = "Полный_отчет_по_заказам.xlsx"
    df_beauty.to_excel(filename, index=False)
    return filename

# --- СИСТЕМА АВТОМАТИЧЕСКИХ ОТЧЕТОВ (РАЗ В 3 ДНЯ) ---
async def auto_report_task():
    while True:
        await asyncio.sleep(259200) 
        
        filename = build_full_excel_report()
        if filename and os.path.exists(filename):
            try:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
                excel_file = FSInputFile(filename)
                await bot.send_document(
                    chat_id=ADMIN_ID, 
                    document=excel_file, 
                    caption=f"📊 <b>Автоматический отчет за 3 дня</b>\n📅 Сформирован: {current_time}\n\nВнутри файлы по всем активным и готовым заказам с формулой потерь металл+9%."
                )
            except Exception as e:
                print(f"Ошибка при отправке автоотчета: {e}")
            finally:
                if os.path.exists(filename):
                    os.remove(filename)

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
    await message.answer("Привет! Я бот-помощник для учета ювелирных заказов.", reply_markup=main_kb)

# --- ВЫГРУЗКА В EXCEL ПО КНОПКЕ ---
@dp.message(F.text == "📊 Отчет в Excel")
async def export_to_excel(message: Message):
    await message.answer("🔄 Формирую ваш личный отчет, подождите немного...")
    
    conn = sqlite3.connect('jewelry_orders.db')
    df = pd.read_sql_query("SELECT * FROM orders WHERE telegram_id = ?", conn, params=(message.from_user.id,))
    conn.close()
    
    if df.empty:
        await message.answer("Ваша база данных пока пуста. Нечего выгружать.")
        return
    
    # Считаем по новой формуле
    df['Потери (+9%), г'] = (df['end_weight'] * 1.09) - df['start_weight']
    df['Потери (+9%), г'] = df['Потери (+9%), г'].round(3)
    
    df_beauty = df.rename(columns={
        'id': 'ID Заказа',
        'client_name': 'Клиент',
        'material': 'Материал / Проба',
        'start_weight': 'Входной вес (г)',
        'start_stones': 'Планируемые камни',
        'end_weight': 'Финальный вес (г)',
        'end_stones': 'Фактические камни',
        'price': 'Стоимость (руб)',
        'status': 'Статус заказа'
    })
    df_beauty = df_beauty.drop(columns=['telegram_id', 'start_photo_id', 'end_photo_id'], errors='ignore')
    
    filename = f"Ювелирные_Заказы_{message.from_user.id}.xlsx"
    df_beauty.to_excel(filename, index=False)
    
    excel_file = FSInputFile(filename)
    await message.answer_document(document=excel_file, caption="📋 Вот твой свежий отчет по твоим заказам (с поправкой +9%)!")
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

# --- СЦЕНАРИЙ: РЕДАКТИРОВАНИЕ ЗАКАЗА ---
@dp.message(F.text == "✏️ Редактировать")
async def start_edit_order(message: Message, state: FSMContext):
    await state.set_state(EditOrder.order_id)
    await message.answer("Введите ID заказа, который нужно изменить:")

@dp.message(EditOrder.order_id)
async def process_edit_id(message: Message, state: FSMContext):
    try:
        order_id = int(message.text)
        conn = sqlite3.connect('jewelry_orders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, client_name, start_photo_id FROM orders WHERE id = ? AND telegram_id = ? AND status = 'В работе'", (order_id, message.from_user.id))
        order = cursor.fetchone()
        conn.close()
        
        if order:
            await state.update_data(order_id=order_id)
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Имя клиента", callback_data="edit_client_name")],
                [InlineKeyboardButton(text="📦 Материал", callback_data="edit_material")],
                [InlineKeyboardButton(text="⚖️ Входной вес", callback_data="edit_start_weight")],
                [InlineKeyboardButton(text="💎 Планируемые камни", callback_data="edit_start_stones")],
                [InlineKeyboardButton(text="📸 Обновить начальное фото", callback_data="edit_start_photo_id")]
            ])
            text = f"Выбран заказ №{order[0]} (Клиент: {order[1]}).\nЧто изменить?"
            if order[2]:
                await message.answer_photo(photo=order[2], caption=text, reply_markup=inline_kb)
            else:
                await message.answer(text, reply_markup=inline_kb)
        else:
            await message.answer("Активный заказ с таким ID в вашем списке не найден.")
            await state.clear()
    except ValueError:
        await message.answer("Введите корректный ID числом:")

@dp.callback_query(F.data.startswith("edit_"))
async def process_edit_choice(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split("_", 1)[1]
    await state.update_data(edit_field=field)
    await state.set_state(EditOrder.waiting_new_value)
    
    fields_ru = {
        "client_name": "новое имя клиента",
        "material": "новый материал/пробу",
        "start_weight": "новый входной вес",
        "start_stones": "новый список камней",
        "start_photo_id": "новое стартовое фото (отправьте его)"
    }
    await callback.message.answer(f"Ожидаю {fields_ru[field]}:")
    await callback.answer()

@dp.message(EditOrder.waiting_new_value)
async def process_new_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data['edit_field']
    order_id = data['order_id']
    
    if field == "start_photo_id":
        if message.photo:
            new_value = message.photo[-1].file_id
        else:
            await message.answer("Пожалуйста, отправьте фото:")
            return
    else:
        new_value = message.text
        if field == "start_weight":
            try:
                new_value = float(new_value.replace(',', '.'))
            except ValueError:
                await message.answer("Ошибка! Вес должен быть числом:")
                return

    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE orders SET {field} = ? WHERE id = ? AND telegram_id = ?", (new_value, order_id, message.from_user.id))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Заказ №{order_id} успешно обновлен!", reply_markup=main_kb)
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
        await state.set_state(CloseOrder.end_stones)
        await message.answer("Какие камни фактически закрепили? (Если нет, напишите 'Нет'):")
    except ValueError:
        await message.answer("Введите вес числом:")

@dp.message(CloseOrder.end_stones)
async def process_end_stones(message: Message, state: FSMContext):
    await state.update_data(end_stones=message.text)
    await state.set_state(CloseOrder.price)
    await message.answer("Введите итоговую стоимость работы (руб):")

@dp.message(CloseOrder.price)
async def process_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(',', '.').replace(' ', ''))
        await state.update_data(price=price)
        await state.set_state(CloseOrder.end_photo)
        await message.answer("Отправьте фото готового изделия или пропустите:", reply_markup=skip_kb)
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
    
    # НОВАЯ ФОРМУЛА ПОТЕРЬ: (готовый вес металла + 9%) - изначальный вес
    loss = round((data['end_weight'] * 1.09) - data['start_weight'], 3)
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE orders 
        SET end_weight = ?, end_stones = ?, price = ?, end_photo_id = ?, status = 'Завершен'
        WHERE id = ? AND telegram_id = ?
    ''', (data['end_weight'], data['end_stones'], data['price'], end_photo_id, data['order_id'], message.from_user.id))
    conn.commit()
    conn.close()
    
    caption = (
        f"🎉 Заказ №{data['order_id']} успешно закрыт!\n\n"
        f"⚖️ Входной вес металла: {data['start_weight']} г\n"
        f"⚖️ Чистый вес готового металла: {data['end_weight']} г\n"
        f"📉 Потери металла (с учетом +9%): {loss} г\n"
        f"💎 Камни (итог): {data['end_stones']}\n"
        f"💰 Стоимость: {data['price']} руб."
    )
    if end_photo_id:
        await message.answer_photo(photo=end_photo_id, caption=caption, reply_markup=main_kb)
    else:
        await message.answer(caption, reply_markup=main_kb)
    await state.clear()

# --- ПРОСМОТР АКТИВНЫХ ЗАКАЗОВ ---
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
    await start_background_web_server()
    asyncio.create_task(auto_report_task())
    print("Бот успешно запущен с обновленной формулой угара!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
