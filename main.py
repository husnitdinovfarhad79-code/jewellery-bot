import asyncio
import sqlite3
import os
import pandas as pd
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- НАСТРОЙКА БОТА ---
TOKEN = "8731687908:AAF1K5UJjSUbY5Nwgv1ye4gTay36i130GMs"
ADMIN_ID = 6360392051  # Твой Telegram ID

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
            item_type TEXT,
            probing TEXT,
            material TEXT,
            size_length TEXT,
            start_weight REAL,
            start_stones TEXT,
            start_photo_id TEXT DEFAULT NULL,
            gold_rate REAL DEFAULT 0.0,
            advance REAL DEFAULT 0.0,
            end_weight REAL DEFAULT NULL,
            end_stones_weight REAL DEFAULT 0.0,
            end_stones TEXT DEFAULT NULL,
            end_photo_id TEXT DEFAULT NULL,
            price REAL DEFAULT NULL,
            status TEXT DEFAULT 'В работе'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            reg_date TEXT,
            pay_till TEXT DEFAULT NULL
        )
    ''')
    conn.commit()
    conn.close()

# --- ПРОВЕРКА ПОДПИСКИ ---
def check_subscription(user_id):
    if user_id == ADMIN_ID:
        return True, "⭐ Администратор"
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT reg_date, pay_till FROM users WHERE telegram_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return False, "Не зарегистрирован"
        
    reg_date = datetime.strptime(user[0], "%Y-%m-%d")
    days_passed = (datetime.now() - reg_date).days
    
    if days_passed <= 3:
        days_left = 3 - days_passed
        return True, f"Пробный период (осталось {days_left} дн.)"
        
    if user[1]:
        pay_till = datetime.strptime(user[1], "%Y-%m-%d")
        if datetime.now() <= pay_till:
            return True, f"Подписка активна до {user[1]}"
            
    return False, "🔴 Срок действия подписки истек"

# --- МИКРО-СЕРВЕР ДЛЯ RENDER ---
async def handle(request): 
    return web.Response(text="Ювелирный бот активен!")

async def start_background_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f" Web-сервер успешно запущен на порту {port}")

# --- АВТОБЭКАПЫ ---
async def backup_scheduler():
    while True:
        await asyncio.sleep(12 * 3600)
        try:
            conn = sqlite3.connect('jewelry_orders.db')
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT telegram_id FROM orders")
            users = cursor.fetchall()
            conn.close()
            for user in users:
                user_id = user[0]
                if check_subscription(user_id)[0]:
                    df_beauty = get_excel_report(user_id=user_id)
                    if df_beauty is not None:
                        filename = f"Резервная_Копия_{user_id}.xlsx"
                        df_beauty.to_excel(filename, index=False)
                        try: await bot.send_document(chat_id=user_id, document=FSInputFile(filename), caption="📦 Автоматический бэкап.")
                        except: pass
                        if os.path.exists(filename): os.remove(filename)
        except Exception as e: print(f"Ошибка бэкапа: {e}")

def get_excel_report(user_id=None):
    conn = sqlite3.connect('jewelry_orders.db')
    df = pd.read_sql_query("SELECT * FROM orders WHERE telegram_id = ?", conn, params=(user_id,))
    conn.close()
    if df.empty: return None
    df['Потери металла, г'] = ((df['end_weight'] * 1.09) - df['start_weight']).round(3)
    df['Потери + Чистый вес, г'] = (df['Потери металла, г'].abs() + df['end_weight']).round(3)
    df['Остаток к оплате (руб)'] = (df['price'] - df['advance']).round(2)
    return df.rename(columns={
        'id': 'ID Заказа', 'client_name': 'Клиент', 'item_type': 'Тип изделия', 'probing': 'Проба',
        'material': 'Материал', 'size_length': 'Размер/Длина', 'start_weight': 'Входной вес (г)', 
        'gold_rate': 'Курс золото', 'advance': 'Аванс', 'end_weight': 'Чистый вес (г)', 
        'end_stones_weight': 'Камни (ct)', 'end_stones': 'Камни завершения', 'price': 'Сумма', 'status': 'Статус'
    }).drop(columns=['telegram_id','start_photo_id','end_photo_id'], errors='ignore')

# --- СОСТОЯНИЯ (FSM) ---
class NewOrder(StatesGroup):
    client_name, item_type, probing, material, size_length, start_weight, start_stones, gold_rate, advance, photo = State(), State(), State(), State(), State(), State(), State(), State(), State(), State()

class CloseOrder(StatesGroup):
    order_id, end_weight, end_stones_weight, end_stones, price, end_photo = State(), State(), State(), State(), State(), State()

class EditOrder(StatesGroup):
    order_id, waiting_new_value = State(), State()

class PaymentState(StatesGroup):
    sender_name = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новый заказ", callback_data="menu_new_order"), 
         InlineKeyboardButton(text="🏁 Завершить заказ", callback_data="menu_close_order")],
        [InlineKeyboardButton(text="📋 Активные заказы", callback_data="menu_active_orders"),
         InlineKeyboardButton(text="✏️ Редактировать", callback_data="menu_edit_order")],
        [InlineKeyboardButton(text="📊 Отчет в Excel", callback_data="menu_excel"),
         InlineKeyboardButton(text="💳 Моя подписка", callback_data="menu_sub_info")]
    ])

back_to_menu_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]])
skip_photo_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏩ Пропустить фото")]], resize_keyboard=True, one_time_keyboard=True)
zero_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="0")]], resize_keyboard=True, one_time_keyboard=True)

# ИСПРАВЛЕННЫЕ РУССКИЕ КНОПКИ БЫСТРЫХ ОТВЕТОВ
item_type_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="Цепь Бисмарк"), KeyboardButton(text="Цепь Якорная")],
    [KeyboardButton(text="Кольцо"), KeyboardButton(text="Обручальное кольцо")],
    [KeyboardButton(text="Серьги"), KeyboardButton(text="Крест"), KeyboardButton(text="Подвеска")],
    [KeyboardButton(text="✏️ Другое (Ввести вручную)")]
], resize_keyboard=True, one_time_keyboard=True)

probing_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="585"), KeyboardButton(text="750")],
    [KeyboardButton(text="925 Серебро"), KeyboardButton(text="⏩ Пропустить")],
    [KeyboardButton(text="✏️ Другая проба")]
], resize_keyboard=True, one_time_keyboard=True)

material_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="Красное золото"), KeyboardButton(text="Лимонное золото")],
    [KeyboardButton(text="Белое золото"), KeyboardButton(text="Серебро 925")],
    [KeyboardButton(text="✏️ Другое (Ввести вручную)")]
], resize_keyboard=True, one_time_keyboard=True)

size_length_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="16.5"), KeyboardButton(text="17.0"), KeyboardButton(text="17.5"), KeyboardButton(text="18.0")],
    [KeyboardButton(text="45 см"), KeyboardButton(text="50 см"), KeyboardButton(text="55 см"), KeyboardButton(text="60 см")],
    [KeyboardButton(text="⏩ Пропустить"), KeyboardButton(text="✏️ Другой размер")]
], resize_keyboard=True, one_time_keyboard=True)

start_stones_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="Без камней"), KeyboardButton(text="Фианиты")],
    [KeyboardButton(text="Бриллианты"), KeyboardButton(text="Изумруд")],
    [KeyboardButton(text="Рубин"), KeyboardButton(text="Сапфир")],
    [KeyboardButton(text="✏️ Свой вариант")]
], resize_keyboard=True, one_time_keyboard=True)

gold_rate_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="5000"), KeyboardButton(text="6000")],
    [KeyboardButton(text="7000"), KeyboardButton(text="8000")],
    [KeyboardButton(text="⏩ Пропустить"), KeyboardButton(text="✏️ Свой курс")]
], resize_keyboard=True, one_time_keyboard=True)

advance_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="0 (Без аванса)"), KeyboardButton(text="3000")],
    [KeyboardButton(text="5000"), KeyboardButton(text="10000")],
    [KeyboardButton(text="✏️ Другая сумма")]
], resize_keyboard=True, one_time_keyboard=True)

end_stones_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="Без изменений"), KeyboardButton(text="Фианиты")],
    [KeyboardButton(text="Бриллианты"), KeyboardButton(text="0")],
    [KeyboardButton(text="✏️ Написать другое")]
], resize_keyboard=True, one_time_keyboard=True)


# Очистка истории сообщений
async def clear_survey_history(state: FSMContext, current_chat_id: int):
    state_data = await state.get_data()
    msg_ids = state_data.get("messages_to_delete", [])
    for msg_id in msg_ids:
        try: await bot.delete_message(chat_id=current_chat_id, message_id=msg_id)
        except: pass
    await state.update_data(messages_to_delete=[])

async def track_message(state: FSMContext, message_id: int):
    state_data = await state.get_data()
    msg_ids = state_data.get("messages_to_delete", [])
    msg_ids.append(message_id)
    await state.update_data(messages_to_delete=msg_ids)

# --- ПРОВЕРКА ДОСТУПА ---
async def has_access_or_alert(event, user_id: int) -> bool:
    has_access, status_msg = check_subscription(user_id)
    if has_access: return True
        
    pay_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Инструкция по оплате", callback_data="show_payment_info")],
        [InlineKeyboardButton(text="✅ Я оплатил(а) 100 руб.", callback_data="start_payment_confirmation")]
    ])
    text = (
        f"⛔ <b>Доступ заблокирован!</b>\n\n"
        f"🔴 Статус: {status_msg}.\n"
        f"Ваш бесплатный пробный период (3 дня) завершился.\n\n"
        f"Для продолжения работы требуется продление подписки.\n"
        f"Стоимость: <b>100 рублей / 30 дней</b>."
    )
    if isinstance(event, Message): await event.answer(text, reply_markup=pay_btn, parse_mode="HTML")
    else: await event.message.edit_text(text, reply_markup=pay_btn, parse_mode="HTML")
    return False

# --- ХЕНДЛЕРЫ КОМАНД ---
@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id, reg_date) VALUES (?, ?)", (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    
    _, status = check_subscription(message.from_user.id)
    await message.answer(
        f"✨ <b>ЮВЕЛИРНЫЙ УЧЕТ v3.0</b> ✨\n\n"
        f"ℹ️ Статус вашей подписки: <b>{status}</b>\n\n"
        f"Выберите действие на панели:", 
        reply_markup=get_main_menu_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _, status = check_subscription(callback.from_user.id)
    await callback.message.edit_text(
        f"✨ <b>ЮВЕЛИРНЫЙ УЧЕТ v3.0</b> ✨\n\n"
        f"ℹ️ Статус вашей подписки: <b>{status}</b>\n\n"
        f"Выберите нужное действие:", 
        reply_markup=get_main_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_sub_info")
async def sub_info_callback(callback: CallbackQuery):
    _, status = check_subscription(callback.from_user.id)
    await callback.message.edit_text(f"📊 <b>Информация о лицензии:</b>\n\nТекущий статус: <b>{status}</b>", reply_markup=back_to_menu_kb, parse_mode="HTML")
    await callback.answer()

# --- СЦЕНАРИИ ОПЛАТЫ ---
@dp.callback_query(F.data == "show_payment_info")
async def payment_info(callback: CallbackQuery):
    text = (
        "💎 <b>Реквизиты для продления подписки:</b>\n\n"
        "Переведите 100 рублей по номеру телефона:\n"
        "<code>+79642480507</code> (Сбербанк / Т-Банк / СБП)\n"
        "Получатель: Администратор бота\n\n"
        "⚠️ После перевода нажмите кнопку ниже, чтобы указать данные платежа."
    )
    confirm_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Я оплатил(а) 100 руб.", callback_data="start_payment_confirmation")]])
    await callback.message.edit_text(text, reply_markup=confirm_btn, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "start_payment_confirmation")
async def ask_payment_sender_name(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentState.sender_name)
    await callback.message.answer("✍️ <b>Введите Имя и Фамилию отправителя</b>:")
    await callback.answer()

@dp.message(PaymentState.sender_name)
async def process_payment_sender_name(message: Message, state: FSMContext):
    sender_info = message.text
    user = message.from_user
    username_text = f"@{user.username}" if user.username else "не установлен"
    admin_confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Продлить на 1 месяц", callback_data=f"activate_sub_{user.id}")]])
    
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 <b>НОВАЯ ЗАЯВКА НА ПОДПИСКУ!</b>\n\n"
                 f"👤 Аккаунт: {user.full_name}\n"
                 f"🔗 Юзернейм: {username_text}\n"
                 f"🆔 ID: <code>{user.id}</code>\n"
                 f"💳 <b>ОТ КОГО ДЕНЬГИ:</b> {sender_info}\n\n"
                 f"Если деньги поступили, нажмите кнопку для активации:",
            reply_markup=admin_confirm_kb, parse_mode="HTML"
        )
        await message.answer("⏳ Данные переданы администратору. Доступ включится после подтверждения.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
    except:
        await message.answer("Ошибка связи. Попробуйте позже.")
    await state.clear()

@dp.callback_query(F.data.startswith("activate_sub_"))
async def admin_activate_sub(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("Запрещено!", show_alert=True)
    target_user_id = int(callback.data.split("_")[2])
    new_pay_till = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET pay_till = ? WHERE telegram_id = ?", (new_pay_till, target_user_id))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(f"✅ Доступ для {target_user_id} продлен до {new_pay_till}!")
    try: await bot.send_message(chat_id=target_user_id, text=f"🎉 <b>Оплата подтверждена!</b> Подписка продлена на 30 дней.", parse_mode="HTML")
    except: pass
    await callback.answer()

# --- ПОШАГОВЫЙ ПРОЦЕСС: НОВЫЙ ЗАКАЗ ---
@dp.callback_query(F.data == "menu_new_order")
async def start_new_order(callback: CallbackQuery, state: FSMContext):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    await state.clear()
    await state.set_state(NewOrder.client_name)
    q_msg = await callback.message.answer("👤 Введите имя или контакты клиента:")
    await track_message(state, q_msg.message_id)
    await callback.answer()

@dp.message(NewOrder.client_name)
async def process_client_name(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    await state.update_data(client_name=message.text)
    await state.set_state(NewOrder.item_type)
    q_msg = await message.answer("💍 Что изготавливаем? Выберите вариант или нажмите ввод вручную:", reply_markup=item_type_kb)
    await track_message(state, q_msg.message_id)

@dp.message(NewOrder.item_type)
async def process_item_type(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Другое (Ввести вручную)":
        q_msg = await message.answer("✍️ Напишите тип изделия вручную:")
        await track_message(state, q_msg.message_id)
        return
        
    await state.update_data(item_type=message.text)
    await state.set_state(NewOrder.probing)
    q_msg = await message.answer("🏷️ Какая проба планируется у изделия?:", reply_markup=probing_kb)
    await track_message(state, q_msg.message_id)

@dp.message(NewOrder.probing)
async def process_probing(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Другая проба":
        q_msg = await message.answer("✍️ Введите пробу вручную:")
        await track_message(state, q_msg.message_id)
        return
    val = "" if message.text == "⏩ Пропустить" else message.text
    await state.update_data(probing=val)
    await state.set_state(NewOrder.material)
    q_msg = await message.answer("🎨 Укажите материал и цвет металла (выберите или нажмите ручной ввод):", reply_markup=material_kb)
    await track_message(state, q_msg.message_id)

@dp.message(NewOrder.material)
async def process_material_step(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Другое (Ввести вручную)":
        q_msg = await message.answer("✍️ Укажите ваш цвет металла/материал вручную:")
        await track_message(state, q_msg.message_id)
        return
        
    await state.update_data(material=message.text)
    await state.set_state(NewOrder.size_length)
    q_msg = await message.answer("📏 Укажите размер или длину изделия (выберите или введите вручную):", reply_markup=size_length_kb)
    await track_message(state, q_msg.message_id)

@dp.message(NewOrder.size_length)
async def process_size_length(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Другой размер":
        q_msg = await message.answer("✍️ Напишите точный размер или длину вручную:")
        await track_message(state, q_msg.message_id)
        return
    val = "" if message.text == "⏩ Пропустить" else message.text
    await state.update_data(size_length=val)
    await state.set_state(NewOrder.start_weight)
    q_msg = await message.answer("⚖️ Введите входной (принятый) вес металла в граммах:")
    await track_message(state, q_msg.message_id)

@dp.message(NewOrder.start_weight)
async def process_start_weight_step(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(start_weight=weight)
        await state.set_state(NewOrder.start_stones)
        q_msg = await message.answer("💎 Какие камни планируются изначально? (Выберите быстрый ответ или ручной ввод):", reply_markup=start_stones_kb)
        await track_message(state, q_msg.message_id)
    except ValueError: 
        q_msg = await message.answer("Введите вес цифрами:")
        await track_message(state, q_msg.message_id)

@dp.message(NewOrder.start_stones)
async def process_start_stones_step(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Свой вариант":
        q_msg = await message.answer("✍️ Опишите планируемые камни вручную:")
        await track_message(state, q_msg.message_id)
        return
    await state.update_data(start_stones=message.text)
    await state.set_state(NewOrder.gold_rate)
    q_msg = await message.answer("📈 Укажите расчетный курс золота за грамм (выберите или введите число):", reply_markup=gold_rate_kb)
    await track_message(state, q_msg.message_id)

@dp.message(NewOrder.gold_rate)
async def process_gold_rate_step(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Свой курс":
        q_msg = await message.answer("✍️ Введите курс металла цифрами вручную:")
        await track_message(state, q_msg.message_id)
        return
    try:
        rate = 0.0 if message.text == "⏩ Пропустить" else float(message.text.replace(',', '.').replace(' ', ''))
        await state.update_data(gold_rate=rate)
        await state.set_state(NewOrder.advance)
        q_msg = await message.answer("💰 Какую сумму аванса внес клиент? (Выберите из вариантов или укажите свою цифру):", reply_markup=advance_kb)
        await track_message(state, q_msg.message_id)
    except ValueError:
        q_msg = await message.answer("Введите курс корректным числом:")
        await track_message(state, q_msg.message_id)

@dp.message(NewOrder.advance)
async def process_advance_step(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Другая сумма":
        q_msg = await message.answer("✍️ Введите точную сумму аванса числом вручную:")
        await track_message(state, q_msg.message_id)
        return
    try:
        clean_text = message.text.replace("0 (Без аванса)", "0").replace(',', '.').replace(' ', '')
        advance = float(clean_text)
        await state.update_data(advance=advance)
        await state.set_state(NewOrder.photo)
        q_msg = await message.answer("📸 Прикрепите фотографию/эскиз или пропустите:", reply_markup=skip_photo_kb)
        await track_message(state, q_msg.message_id)
    except ValueError: 
        q_msg = await message.answer("Введите сумму аванса числом:")
        await track_message(state, q_msg.message_id)

@dp.message(NewOrder.photo, F.photo)
async def process_photo_step(message: Message, state: FSMContext): 
    await track_message(state, message.message_id)
    await save_order_to_db(message.photo[-1].file_id, message, state)

@dp.message(NewOrder.photo, F.text == "⏩ Пропустить фото")
async def process_skip_photo_step(message: Message, state: FSMContext): 
    await track_message(state, message.message_id)
    await save_order_to_db(None, message, state)

async def save_order_to_db(photo_id, message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (
            telegram_id, client_name, item_type, probing, material, 
            size_length, start_weight, start_stones, start_photo_id, gold_rate, advance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        message.from_user.id, data['client_name'], data['item_type'], data['probing'], data['material'],
        data['size_length'], data['start_weight'], data['start_stones'], photo_id, data['gold_rate'], data['advance']
    ))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    await clear_survey_history(state, message.chat.id)
    
    caption = (
        f"✅ Заказ успешно создан!\n"
        f"🆔 ID заказа: {order_id}\n"
        f"👤 Клиент: {data['client_name']}\n"
        f"💍 Изделие: {data['item_type']} ({data['probing']} проба)\n"
        f"📦 Материал: {data['material']}\n"
        f"📏 Размер/Длина: {data['size_length']}\n"
        f"⚖️ Входной вес: {data['start_weight']} г\n"
        f"💎 Камни: {data['start_stones']}\n"
        f"📈 Курс металла: {data['gold_rate']} руб.\n"
        f"💰 Аванс: {data['advance']} руб."
    )
    await (message.answer_photo(photo=photo_id, caption=caption) if photo_id else message.answer(caption))
    await state.clear()
    await message.answer("Возврат к панели:", reply_markup=get_main_menu_kb())

# --- ПОШАГОВЫЙ ПРОЦЕСС: ЗАВЕРШЕНИЕ ЗАКАЗА ---
@dp.callback_query(F.data == "menu_close_order")
async def start_close_order_callback(callback: CallbackQuery, state: FSMContext):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    await state.clear()
    await state.set_state(CloseOrder.order_id)
    q_msg = await callback.message.answer("🏁 Введите ID заказа для его закрытия:")
    await track_message(state, q_msg.message_id)
    await callback.answer()

@dp.message(CloseOrder.order_id)
async def process_close_id(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    try:
        order_id = int(message.text)
        conn = sqlite3.connect('jewelry_orders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT client_name, start_weight, advance, start_stones, gold_rate FROM orders WHERE id = ? AND telegram_id = ? AND status = 'В работе'", (order_id, message.from_user.id))
        order = cursor.fetchone()
        conn.close()
        if order:
            await state.update_data(order_id=order_id, start_weight=order[1], advance=order[2], start_stones_name=order[3], gold_rate=order[4])
            await state.set_state(CloseOrder.end_weight)
            q_msg = await message.answer(f"Введите чистый вес готового металла (в граммах):")
            await track_message(state, q_msg.message_id)
        else: 
            q_msg = await message.answer("Заказ не найден.")
            await track_message(state, q_msg.message_id)
    except ValueError: 
        q_msg = await message.answer("ID должен быть числом:")
        await track_message(state, q_msg.message_id)

@dp.message(CloseOrder.end_weight)
async def process_end_weight(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    try:
        await state.update_data(end_weight=float(message.text.replace(',', '.')))
        await state.set_state(CloseOrder.end_stones_weight)
        q_msg = await message.answer("Введите вес закрепленных камней в граммах (или 0):", reply_markup=zero_kb)
        await track_message(state, q_msg.message_id)
    except ValueError: 
        q_msg = await message.answer("Введите чистый вес числом:")
        await track_message(state, q_msg.message_id)

@dp.message(CloseOrder.end_stones_weight)
async def process_end_stones_weight(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    try:
        await state.update_data(end_stones_weight=float(message.text.replace(',', '.')))
        await state.set_state(CloseOrder.end_stones)
        q_msg = await message.answer("Введите описание закрепленных камней (выберите или напишите вручную):", reply_markup=end_stones_kb)
        await track_message(state, q_msg.message_id)
    except ValueError: 
        q_msg = await message.answer("Введите вес камней числом:")
        await track_message(state, q_msg.message_id)

@dp.message(CloseOrder.end_stones)
async def process_end_stones(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    if message.text == "✏️ Написать другое":
        q_msg = await message.answer("✍️ Опишите закрепленные камни вручную:")
        await track_message(state, q_msg.message_id)
        return
    await state.update_data(end_stones=message.text)
    await state.set_state(CloseOrder.price)
    q_msg = await message.answer("Укажите итоговую стоимость работы (руб):")
    await track_message(state, q_msg.message_id)

@dp.message(CloseOrder.price)
async def process_price(message: Message, state: FSMContext):
    await track_message(state, message.message_id)
    try:
        await state.update_data(price=float(message.text.replace(',', '.').replace(' ', '')))
        await state.set_state(CloseOrder.end_photo)
        q_msg = await message.answer("📸 Отправьте фото изделия или пропустите:", reply_markup=skip_photo_kb)
        await track_message(state, q_msg.message_id)
    except ValueError: 
        q_msg = await message.answer("Укажите цену цифрами:")
        await track_message(state, q_msg.message_id)

@dp.message(CloseOrder.end_photo, F.photo)
async def process_end_photo(message: Message, state: FSMContext): 
    await track_message(state, message.message_id)
    await finalize_order(message.photo[-1].file_id, message, state)

@dp.message(CloseOrder.end_photo, F.text == "⏩ Пропустить фото")
async def process_skip_end_photo(message: Message, state: FSMContext): 
    await track_message(state, message.message_id)
    await finalize_order(None, message, state)

async def finalize_order(end_photo_id, message: Message, state: FSMContext):
    data = await state.get_data()
    
    # РАСЧЕТЫ ВЕСА
    loss = round((data['end_weight'] * 1.09) - data['start_weight'], 3)
    total_metal = round(bytes(abs(loss)) + data['end_weight'], 3) if hasattr(data, 'abs') else round(abs(loss) + data['end_weight'], 3)
    
    # Формируем строку потерь со скобками в рублях, если ушли в плюс
    loss_str = f"{loss} г"
    if loss > 0:
        rub_value = round(loss * data['gold_rate'], 2)
        loss_str += f" (+{rub_value} руб.)"
        
    # ИТОГОВАЯ СУММА К ОПЛАТЕ: Стоимость работы МИНУС аванс
    final_price = round(data['price'] - data['advance'], 2)
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET end_weight=?, end_stones_weight=?, end_stones=?, price=?, end_photo_id=?, status="Завершен" WHERE id=? AND telegram_id=?', (data['end_weight'], data['end_stones_weight'], data['end_stones'], final_price, end_photo_id, data['order_id'], message.from_user.id))
    conn.commit()
    conn.close()
    
    await clear_survey_history(state, message.chat.id)
    
    caption = (
        f"🎉 Заказ №{data['order_id']} успешно закрыт!\n\n"
        f"⚖️ Входной вес металла: {data['start_weight']} г\n"
        f"⚖️ Чистый вес готового металла: {data['end_weight']} г\n"
        f"💎 Вес закрепленных камней: {data['end_stones_weight']} г ({data['end_stones']})\n"
        f"📉 Потери (металл +9%): {loss_str}\n"
        f"📊 Итог (Потери + Чистый вес): {total_metal} г\n"
        f"💰 Сумма: {final_price} руб."
    )
    await (message.answer_photo(photo=end_photo_id, caption=caption) if end_photo_id else message.answer(caption))
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_menu_kb())

# --- ОСТАЛЬНЫЕ ФУНКЦИИ ---
@dp.callback_query(F.data == "menu_active_orders")
async def show_active_orders_callback(callback: CallbackQuery):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_name, item_type, material, start_weight FROM orders WHERE status = 'В работе' AND telegram_id = ?", (callback.from_user.id,))
    orders = cursor.fetchall()
    conn.close()
    if not orders: return await callback.message.edit_text("📭 Активных заказов нет.", reply_markup=back_to_menu_kb)
    response = "📋 <b>Список изделий в работе:</b>\n\n"
    for o in orders: response += f"🆔 <b>ID: {o[0]}</b> | 👤 {o[1]} | 💍 {o[2]} | 🎨 {o[3]} | ⚖️ {o[4]}г\n"
    await callback.message.edit_text(response, reply_markup=back_to_menu_kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "menu_excel")
async def export_to_excel_callback(callback: CallbackQuery):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    df_beauty = get_excel_report(user_id=callback.from_user.id)
    if df_beauty is None: return await callback.message.edit_text("База пуста.", reply_markup=back_to_menu_kb)
    filename = f"Ювелирные_Заказы_{callback.from_user.id}.xlsx"
    df_beauty.to_excel(filename, index=False)
    await callback.message.answer_document(document=FSInputFile(filename), caption="📋 Ваш отчет Excel!")
    if os.path.exists(filename): os.remove(filename)
    await callback.answer()

@dp.callback_query(F.data == "menu_edit_order")
async def start_edit_order_callback(callback: CallbackQuery, state: FSMContext):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    await state.set_state(EditOrder.order_id)
    await callback.message.answer("✏️ Введите ID активного заказа для изменения:")
    await callback.answer()

@dp.message(EditOrder.order_id)
async def process_edit_id(message: Message, state: FSMContext):
    try:
        order_id = int(message.text)
        conn = sqlite3.connect('jewelry_orders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, client_name FROM orders WHERE id = ? AND telegram_id = ? AND status = 'В работе'", (order_id, message.from_user.id))
        order = cursor.fetchone()
        conn.close()
        if order:
            await state.update_data(order_id=order_id)
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Изменить ФИО", callback_data="edit_client_name")],
                [InlineKeyboardButton(text="💍 Изменить Тип изделия", callback_data="edit_item_type")],
                [InlineKeyboardButton(text="🎨 Изменить Материал", callback_data="edit_material")],
                [InlineKeyboardButton(text="⚖️ Изменить Входной вес", callback_data="edit_start_weight")]
            ])
            await message.answer(f"Выбран заказ №{order[0]}. Выберите поле:", reply_markup=inline_kb)
        else: await message.answer("Заказ не найден.")
    except ValueError: await message.answer("ID должен быть числом:")

@dp.callback_query(F.data.startswith("edit_"))
async def process_edit_choice(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split("_", 1)[1]
    await state.update_data(edit_field=field)
    await state.set_state(EditOrder.waiting_new_value)
    await callback.message.answer(f"Введите новое значение:")
    await callback.answer()

@dp.message(EditOrder.waiting_new_value)
async def process_new_value(message: Message, state: FSMContext):
    data = await state.get_data()
    val = message.text
    if data['edit_field'] == "start_weight": val = float(val.replace(',', '.'))
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute(f"UPDATE orders SET {data['edit_field']} = ? WHERE id = ? AND telegram_id = ?", (val, data['order_id'], message.from_user.id))
    conn.commit()
    conn.close()
    await message.answer("✅ База обновлена!", reply_markup=get_main_menu_kb())
    await state.clear()

async def main():
    init_db()
    await start_background_web_server()
    asyncio.create_task(backup_scheduler())
    print(" Бот запускает Polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
