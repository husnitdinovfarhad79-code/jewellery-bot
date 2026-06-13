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
ADMIN_ID = 6360392051  # Твой Telegram ID для получения заявок

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    # Таблица заказов со всеми расширенными полями
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
    # Таблица пользователей для подписки
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            reg_date TEXT,
            pay_till TEXT DEFAULT NULL
        )
    ''')
    
    # Проверка структуры и миграции (чтобы старая БД не ломалась)
    cursor.execute("PRAGMA table_info(orders)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'item_type' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN item_type TEXT DEFAULT ''")
    if 'probing' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN probing TEXT DEFAULT ''")
    if 'size_length' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN size_length TEXT DEFAULT ''")
    if 'gold_rate' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN gold_rate REAL DEFAULT 0.0")
    if 'advance' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN advance REAL DEFAULT 0.0")
    if 'end_stones_weight' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN end_stones_weight REAL DEFAULT 0.0")
        
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
    
    # ТЕСТОВЫЙ ПЕРИОД (3 дня для твоей проверки)
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
    return web.Response(text="Ювелирный бот активен и развернут на полную длину!")

async def start_background_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, '0.0.0.0', port).start()

# --- АВТОМАТИЧЕСКИЕ БЭКАПЫ РАЗ В 12 ЧАСОВ ---
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
                has_access, _ = check_subscription(user_id)
                if has_access:
                    df_beauty = get_excel_report(user_id=user_id)
                    if df_beauty is not None:
                        filename = f"Резервная_Копия_{user_id}.xlsx"
                        df_beauty.to_excel(filename, index=False)
                        try: 
                            await bot.send_document(chat_id=user_id, document=FSInputFile(filename), caption="📦 Автоматический бэкап вашей базы заказов.")
                        except: pass
                        if os.path.exists(filename): os.remove(filename)
        except Exception as e: 
            print(f"Ошибка планировщика бэкапов: {e}")

# --- СБОРКА ТАБЛИЦЫ EXCEL ---
def get_excel_report(user_id=None):
    conn = sqlite3.connect('jewelry_orders.db')
    df = pd.read_sql_query("SELECT * FROM orders WHERE telegram_id = ?", conn, params=(user_id,))
    conn.close()
    if df.empty: return None
    df['Потери металла, г'] = ((df['end_weight'] * 1.09) - df['start_weight']).round(3)
    df['Потери + Чистый вес, г'] = (df['Потери металла, г'].abs() + df['end_weight']).round(3)
    df['Остаток к оплате (руб)'] = (df['price'] - df['advance']).round(2)
    return df.rename(columns={
        'id': 'ID Заказа', 
        'client_name': 'Клиент', 
        'item_type': 'Тип изделия',
        'probing': 'Проба',
        'material': 'Материал/Цвет',
        'size_length': 'Размер/Длина',
        'start_weight': 'Входной вес (г)', 
        'gold_rate': 'Курс золота', 
        'advance': 'Аванс (руб)', 
        'end_weight': 'Чистый вес изделия (г)', 
        'end_stones_weight': 'Вес камней (ct)', 
        'end_stones': 'Закрепленные камни',
        'price': 'Итоговая стоимость', 
        'status': 'Статус заказа'
    }).drop(columns=['telegram_id','start_photo_id','end_photo_id'], errors='ignore')

# --- СОСТОЯНИЯ (FSM) ДЛЯ НОВОГО ЗАКАЗА ---
class NewOrder(StatesGroup):
    client_name = State()
    item_type = State()
    probing = State()
    material = State()
    size_length = State()
    start_weight = State()
    start_stones = State()
    gold_rate = State()
    advance = State()
    photo = State()

# СОСТОЯНИЯ ДЛЯ ЗАКРЫТИЯ ЗАКАЗА
class CloseOrder(StatesGroup):
    order_id = State()
    end_weight = State()
    end_stones_weight = State()
    end_stones = State()
    price = State()
    end_photo = State()

# СОСТОЯНИЯ ДЛЯ РЕДАКТИРОВАНИЯ
class EditOrder(StatesGroup):
    order_id = State()
    waiting_new_value = State()

# СОСТОЯНИЕ ДЛЯ ОПЛАТЫ
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

# Готовые шаблоны ответов кнопками в чате (ReplyKeyboardMarkup)
skip_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏩ Пропустить")]], resize_keyboard=True, one_time_keyboard=True)
skip_photo_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏩ Пропустить фото")]], resize_keyboard=True, one_time_keyboard=True)
zero_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="0")]], resize_keyboard=True, one_time_keyboard=True)
no_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Нет")]], resize_keyboard=True, one_time_keyboard=True)

# Кнопки быстрого выбора для пробы
probing_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="585"), KeyboardButton(text="750")],
    [KeyboardButton(text="925 Серебро"), KeyboardButton(text="⏩ Пропустить")]
], resize_keyboard=True, one_time_keyboard=True)

# --- МИДЛВАРЬ ПРОВЕРКИ ДОСТУПА ---
async def has_access_or_alert(event, user_id: int) -> bool:
    has_access, status_msg = check_subscription(user_id)
    if has_access: return True
        
    pay_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Инструкция по оплате", callback_data="show_payment_info")],
        [InlineKeyboardButton(text="✅ Я оплатил(а) 100 руб.", callback_data="start_payment_confirmation")]
    ])
    text = (
        f"⛔ <b>Доступ ограничен!</b>\n\n"
        f"🔴 Текущий статус: {status_msg}.\n"
        f"Ваш бесплатный пробный период (3 дня) подошел к концу.\n\n"
        f"Чтобы продолжить вести учет заказов и пользоваться базой, продлите подписку.\n"
        f"Стоимость: <b>100 рублей на 30 дней</b>."
    )
    if isinstance(event, Message): await event.answer(text, reply_markup=pay_btn, parse_mode="HTML")
    else: await event.message.edit_text(text, reply_markup=pay_btn, parse_mode="HTML")
    return False

# --- ОБРАБОТКА КОМАНД СТАРТА И МЕНЮ ---
@dp.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # Принудительный сброс зависших сессий опросов
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id, reg_date) VALUES (?, ?)", (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    
    _, status = check_subscription(message.from_user.id)
    await message.answer(
        f"✨ <b>ЮВЕЛИРНЫЙ УЧЕТ v3.0 (Полная версия)</b> ✨\n\n"
        f"Персональная база данных ваших ювелирных изделий и расчетов.\n"
        f"ℹ️ Статус подписки: <b>{status}</b>\n\n"
        f"Управляйте заказами через панель управления:", 
        reply_markup=get_main_menu_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _, status = check_subscription(callback.from_user.id)
    await callback.message.edit_text(
        f"✨ <b>ЮВЕЛИРНЫЙ УЧЕТ v3.0 (Полная версия)</b> ✨\n\n"
        f"ℹ️ Статус подписки: <b>{status}</b>\n\n"
        f"Выберите нужное действие:", 
        reply_markup=get_main_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_sub_info")
async def sub_info_callback(callback: CallbackQuery):
    _, status = check_subscription(callback.from_user.id)
    await callback.message.edit_text(f"📊 <b>Параметры вашей лицензии:</b>\n\nТекущее состояние: <b>{status}</b>", reply_markup=back_to_menu_kb, parse_mode="HTML")
    await callback.answer()

# --- ИНЛАЙН СЦЕНАРИИ ОПЛАТЫ И ПОДТВЕРЖДЕНИЯ ---
@dp.callback_query(F.data == "show_payment_info")
async def payment_info(callback: CallbackQuery):
    text = (
        "💎 <b>Реквизиты для перевода (100 руб / месяц):</b>\n\n"
        "Сделайте перевод на сумму 100 рублей по номеру телефона:\n"
        "<code>+79642480507</code> (Сбербанк / Т-Банк / СБП)\n"
        "Получатель: Администратор бота\n\n"
        "⚠️ После отправки денег нажмите кнопку ниже, чтобы ввести имя отправителя."
    )
    confirm_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Я оплатил(а) 100 руб.", callback_data="start_payment_confirmation")]])
    await callback.message.edit_text(text, reply_markup=confirm_btn, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "start_payment_confirmation")
async def ask_payment_sender_name(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentState.sender_name)
    await callback.message.answer("✍️ <b>Введите Имя и Фамилию отправителя</b> (как в чеке/банке), чтобы администратор мгновенно идентифицировал ваш платеж:")
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
            text=f"🔔 <b>НОВАЯ ЗАЯВКА НА ПОДТВЕРЖДЕНИЕ!</b>\n\n"
                 f"👤 Аккаунт: {user.full_name}\n"
                 f"🔗 Ссылка на профиль: {username_text}\n"
                 f"🆔 Telegram ID: <code>{user.id}</code>\n"
                 f"💳 <b>ОТ КОГО ДЕНЬГИ (указано юзером):</b> {sender_info}\n\n"
                 f"Сверьте данные в банковском приложении. Если 100 рублей пришли, нажмите кнопку ниже:",
            reply_markup=admin_confirm_kb, parse_mode="HTML"
        )
        await message.answer("⏳ Спасибо! Ваши данные переданы на верификацию администратору. Бот автоматически активируется после подтверждения.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/start")]], resize_keyboard=True))
    except:
        await message.answer("Ошибка отправки заявки. Пожалуйста, попробуйте написать имя повторно.")
    await state.clear()

@dp.callback_query(F.data.startswith("activate_sub_"))
async def admin_activate_sub(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("У вас нет прав администратора!", show_alert=True)
    target_user_id = int(callback.data.split("_")[2])
    new_pay_till = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET pay_till = ? WHERE telegram_id = ?", (new_pay_till, target_user_id))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(f"✅ Доступ для пользователя {target_user_id} успешно продлен до {new_pay_till}!")
    try: 
        await bot.send_message(chat_id=target_user_id, text=f"🎉 <b>Оплата успешно подтверждена!</b>\nВаша подписка продлена на 30 дней (до {new_pay_till}). Отправьте /start для обновления меню!", parse_mode="HTML")
    except: pass
    await callback.answer()

# --- ПОЛНЫЙ ПОШАГОВЫЙ ПРОЦЕСС: СОЗДАНИЕ ЗАКАЗА ---
@dp.callback_query(F.data == "menu_new_order")
async def start_new_order(callback: CallbackQuery, state: FSMContext):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    await state.clear()
    await state.set_state(NewOrder.client_name)
    await callback.message.answer("👤 <b>Шаг 1 из 10:</b> Введите ФИО или контакты клиента:")
    await callback.answer()

@dp.message(NewOrder.client_name)
async def process_client_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await state.set_state(NewOrder.item_type)
    await message.answer("💍 <b>Шаг 2 из 10:</b> Что изготавливаем? (Тип изделия, например: <i>Цепь Бисмарк, Кольцо, Серьги</i>):")

@dp.message(NewOrder.item_type)
async def process_item_type(message: Message, state: FSMContext):
    await state.update_data(item_type=message.text)
    await state.set_state(NewOrder.probing)
    await message.answer("🏷️ <b>Шаг 3 из 10:</b> Какая проба планируется у изделия?:", reply_markup=probing_kb)

@dp.message(NewOrder.probing)
async def process_probing(message: Message, state: FSMContext):
    val = "" if message.text == "⏩ Пропустить" else message.text
    await state.update_data(probing=val)
    await state.set_state(NewOrder.material)
    await message.answer("🎨 <b>Шаг 4 из 10:</b> Укажите материал и цвет металла (например: <i>Красное золото, Белое золото, Лом клиента</i>):")

@dp.message(NewOrder.material)
async def process_material_step(message: Message, state: FSMContext):
    await state.update_data(material=message.text)
    await state.set_state(NewOrder.size_length)
    await message.answer("📏 <b>Шаг 5 из 10:</b> Укажите размер или длину изделия (например: <i>Размер 18.5 / Длина 55 см</i>):", reply_markup=skip_kb)

@dp.message(NewOrder.size_length)
async def process_size_length(message: Message, state: FSMContext):
    val = "" if message.text == "⏩ Пропустить" else message.text
    await state.update_data(size_length=val)
    await state.set_state(NewOrder.start_weight)
    await message.answer("⚖️ <b>Шаг 6 из 10:</b> Введите входной (принятый) вес металла в граммах:")

@dp.message(NewOrder.start_weight)
async def process_start_weight_step(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
        await state.update_data(start_weight=weight)
        await state.set_state(NewOrder.start_stones)
        await message.answer("💎 <b>Шаг 7 из 10:</b> Какие камни предоставляет клиент или планируются изначально?:", reply_markup=no_kb)
    except ValueError: 
        await message.answer("Пожалуйста, введите числовое значение веса:")

@dp.message(NewOrder.start_stones)
async def process_start_stones_step(message: Message, state: FSMContext):
    await state.update_data(start_stones=message.text)
    await state.set_state(NewOrder.gold_rate)
    await message.answer("📈 <b>Шаг 8 из 10:</b> Укажите расчетный курс золота за грамм для этого заказа (руб):", reply_markup=skip_kb)

@dp.message(NewOrder.gold_rate)
async def process_gold_rate_step(message: Message, state: FSMContext):
    rate = 0.0 if message.text == "⏩ Пропустить" else float(message.text.replace(',', '.').replace(' ', ''))
    await state.update_data(gold_rate=rate)
    await state.set_state(NewOrder.advance)
    await message.answer("💰 <b>Шаг 9 из 10:</b> Какую сумму аванса внес клиент (если нет аванса — введите 0):", reply_markup=zero_kb)

@dp.message(NewOrder.advance)
async def process_advance_step(message: Message, state: FSMContext):
    try:
        advance = float(message.text.replace(',', '.').replace(' ', ''))
        await state.update_data(advance=advance)
        await state.set_state(NewOrder.photo)
        await message.answer("📸 <b>Шаг 10 из 10:</b> Прикрепите фотографию/эскиз изделия или лома:", reply_markup=skip_photo_kb)
    except ValueError: 
        await message.answer("Введите сумму аванса числом:")

@dp.message(NewOrder.photo, F.photo)
async def process_photo_step(message: Message, state: FSMContext): 
    await save_order_to_db(message.photo[-1].file_id, message, state)

@dp.message(NewOrder.photo, F.text == "⏩ Пропустить фото")
async def process_skip_photo_step(message: Message, state: FSMContext): 
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
    
    caption = (
        f"✅ <b>Заказ №{order_id} успешно внесен в работу!</b>\n\n"
        f"👤 Клиент: {data['client_name']}\n"
        f"💍 Изделие: {data['item_type']} ({data['probing']} проба)\n"
        f"📦 Материал: {data['material']}\n"
        f"📏 Размер: {data['size_length']}\n"
        f"⚖️ Входной вес лома: {data['start_weight']} г"
    )
    await (message.answer_photo(photo=photo_id, caption=caption, parse_mode="HTML") if photo_id else message.answer(caption, parse_mode="HTML"))
    await state.clear()
    await message.answer("Возврат к панели управления заказами:", reply_markup=get_main_menu_kb())

# --- ПОЛНЫЙ ПОШАГОВЫЙ ПРОЦЕСС: ЗАВЕРШЕНИЕ ЗАКАЗА ---
@dp.callback_query(F.data == "menu_close_order")
async def start_close_order_callback(callback: CallbackQuery, state: FSMContext):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    await state.set_state(CloseOrder.order_id)
    await callback.message.answer("🏁 Введите уникальный ID заказа для его окончательного расчета и закрытия:")
    await callback.answer()

@dp.message(CloseOrder.order_id)
async def process_close_id(message: Message, state: FSMContext):
    try:
        order_id = int(message.text)
        conn = sqlite3.connect('jewelry_orders.db')
        cursor = conn.cursor()
        cursor.execute("SELECT client_name, start_weight, start_photo_id, advance FROM orders WHERE id = ? AND telegram_id = ? AND status = 'В работе'", (order_id, message.from_user.id))
        order = cursor.fetchone()
        conn.close()
        if order:
            await state.update_data(order_id=order_id, start_weight=order[1], advance=order[3])
            await state.set_state(CloseOrder.end_weight)
            await message.answer(f"📦 Заказ №{order_id} ({order[0]}).\nВведите чистый вес готового металлического изделия (в граммах):")
        else: await message.answer("Заказ с таким ID отсутствует в списке активных.")
    except ValueError: await message.answer("ID заказа должен состоять только из цифр:")

@dp.message(CloseOrder.end_weight)
async def process_end_weight(message: Message, state: FSMContext):
    try:
        await state.update_data(end_weight=float(message.text.replace(',', '.')))
        await state.set_state(CloseOrder.end_stones_weight)
        await message.answer("💎 Введите суммарный вес закрепленных ювелирных камней в граммах (бот переведет в ct):", reply_markup=zero_kb)
    except ValueError: await message.answer("Введите вес чистого металла числом:")

@dp.message(CloseOrder.end_stones_weight)
async def process_end_stones_weight(message: Message, state: FSMContext):
    try:
        await state.update_data(end_stones_weight=round(float(message.text.replace(',', '.')) * 5.0, 3))
        await state.set_state(CloseOrder.end_stones)
        await message.answer("✏️ Опишите, какие именно камни были закреплены (например: <i>Фианиты 2мм 5шт</i>):", reply_markup=no_kb)
    except ValueError: await message.answer("Укажите вес камней числовым значением:")

@dp.message(CloseOrder.end_stones)
async def process_end_stones(message: Message, state: FSMContext):
    await state.update_data(end_stones=message.text)
    await state.set_state(CloseOrder.price)
    await message.answer("💰 Укажите полную финальную стоимость работы (руб):")

@dp.message(CloseOrder.price)
async def process_price(message: Message, state: FSMContext):
    try:
        await state.update_data(price=float(message.text.replace(',', '.').replace(' ', '')))
        await state.set_state(CloseOrder.end_photo)
        await message.answer("📸 Загрузите фотографию готового ювелирного изделия или пропустите этот шаг:", reply_markup=skip_photo_kb)
    except ValueError: await message.answer("Укажите стоимость работы цифрами:")

@dp.message(CloseOrder.end_photo, F.photo)
async def process_end_photo(message: Message, state: FSMContext): await finalize_order(message.photo[-1].file_id, message, state)
@dp.message(CloseOrder.end_photo, F.text == "⏩ Пропустить фото")
async def process_skip_end_photo(message: Message, state: FSMContext): await finalize_order(None, message, state)

async def finalize_order(end_photo_id, message: Message, state: FSMContext):
    data = await state.get_data()
    loss = round((data['end_weight'] * 1.09) - data['start_weight'], 3)
    total_metal = round(abs(loss) + data['end_weight'], 3)
    to_pay = round(data['price'] - data['advance'], 2)
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET end_weight=?, end_stones_weight=?, end_stones=?, price=?, end_photo_id=?, status="Завершен" WHERE id=? AND telegram_id=?', (data['end_weight'], data['end_stones_weight'], data['end_stones'], data['price'], end_photo_id, data['order_id'], message.from_user.id))
    conn.commit()
    conn.close()
    
    caption = (
        f"🎉 <b>Заказ №{data['order_id']} успешно выполнен и закрыт!</b>\n\n"
        f"📉 Потери металла (с учетом угара 9%): <b>{loss} г</b>\n"
        f"📊 Общий расход чистого металла: <b>{total_metal} г</b>\n"
        f"💵 Итоговый расчет с клиентом: <b>{to_pay} руб.</b> к оплате."
    )
    await (message.answer_photo(photo=end_photo_id, caption=caption, parse_mode="HTML") if end_photo_id else message.answer(caption, parse_mode="HTML"))
    await state.clear()
    await message.answer("Главное меню управления:", reply_markup=get_main_menu_kb())

# --- СПИСОК АКТИВНЫХ ЗАКАЗОВ ---
@dp.callback_query(F.data == "menu_active_orders")
async def show_active_orders_callback(callback: CallbackQuery):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_name, item_type, material, start_weight FROM orders WHERE status = 'В работе' AND telegram_id = ?", (callback.from_user.id,))
    orders = cursor.fetchall()
    conn.close()
    if not orders: return await callback.message.edit_text("📭 На текущий момент у вас нет активных заказов.", reply_markup=back_to_menu_kb)
    response = "📋 <b>Список изделий в работе:</b>\n\n"
    for o in orders: response += f"🆔 <b>ID: {o[0]}</b> | 👤 {o[1]} | 💍 {o[2]} | 🎨 {o[3]} | ⚖️ {o[4]}г\n"
    await callback.message.edit_text(response, reply_markup=back_to_menu_kb, parse_mode="HTML")
    await callback.answer()

# --- ВЫГРУЗКА В EXCEL ---
@dp.callback_query(F.data == "menu_excel")
async def export_to_excel_callback(callback: CallbackQuery):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    df_beauty = get_excel_report(user_id=callback.from_user.id)
    if df_beauty is None: return await callback.message.edit_text("Ваша база данных пуста, нечего выгружать.", reply_markup=back_to_menu_kb)
    filename = f"Ювелирные_Заказы_{callback.from_user.id}.xlsx"
    df_beauty.to_excel(filename, index=False)
    await callback.message.answer_document(document=FSInputFile(filename), caption="📋 Ваш актуальный отчет в формате Excel таблицы!")
    if os.path.exists(filename): os.remove(filename)
    await callback.answer()

# --- РЕДАКТИРОВАНИЕ ЗАКАЗА ---
@dp.callback_query(F.data == "menu_edit_order")
async def start_edit_order_callback(callback: CallbackQuery, state: FSMContext):
    if not await has_access_or_alert(callback, callback.from_user.id): return
    await state.set_state(EditOrder.order_id)
    await callback.message.answer("✏️ Введите числовой ID активного заказа для изменения данных:")
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
            await message.answer(f"Выбран заказ №{order[0]} (Клиент: {order[1]}). Выберите поле:", reply_markup=inline_kb)
        else: await message.answer("Заказ не найден.")
    except ValueError: await message.answer("ID должен быть числом:")

@dp.callback_query(F.data.startswith("edit_"))
async def process_edit_choice(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split("_", 1)[1]
    await state.update_data(edit_field=field)
    await state.set_state(EditOrder.waiting_new_value)
    await callback.message.answer(f"Введите новое значение для этого поля:")
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
    await message.answer("✅ Информация в базе данных успешно обновлена!", reply_markup=get_main_menu_kb())
    await state.clear()

# --- ИНИЦИАЛИЗАЦИЯ ВСЕХ СЛУЖБ БОТА ---
async def main():
    init_db()
    await start_background_web_server()
    asyncio.create_task(backup_scheduler())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
