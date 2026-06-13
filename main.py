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
# Твой Telegram ID для подтверждения оплат
ADMIN_ID = 6360392051  

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    # Таблица заказов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            client_name TEXT,
            material TEXT,
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
    # Таблица пользователей для контроля подписки
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            reg_date TEXT,
            pay_till TEXT DEFAULT NULL
        )
    ''')
    
    # Проверка структуры (миграция для старых баз)
    cursor.execute("PRAGMA table_info(orders)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'gold_rate' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN gold_rate REAL DEFAULT 0.0")
    if 'advance' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN advance REAL DEFAULT 0.0")
    if 'end_stones_weight' not in columns: cursor.execute("ALTER TABLE orders ADD COLUMN end_stones_weight REAL DEFAULT 0.0")
        
    conn.commit()
    conn.close()

# --- ПРОВЕРКА ПОДПИСКИ ---
def check_subscription(user_id):
    """Возвращает (bool: доступ, str: статус/сообщение)"""
    if user_id == ADMIN_ID:
        return True, "Администратор"
        
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT reg_date, pay_till FROM users WHERE telegram_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return False, "Not registered"
        
    reg_date = datetime.strptime(user[0], "%Y-%m-%d")
    days_passed = (datetime.now() - reg_date).days
    
    # 1. Проверяем бесплатный пробный период (3 дня для теста)
    if days_passed <= 1:
        days_left = 1 - days_passed
        return True, f"Пробный период (осталось {days_left} дн.)"
        
    # 2. Проверяем платную подписку
    if user[1]:
        pay_till = datetime.strptime(user[1], "%Y-%m-%d")
        if datetime.now() <= pay_till:
            return True, f"Подписка активна до {user[1]}"
            
    return False, "Срок действия подписки истек"

# --- МИКРО-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Ювелирный бот с подпиской активен!")

async def start_background_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- БЭКАПЫ ПОДПИСЧИКАМ ---
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
                            await bot.send_document(chat_id=user_id, document=FSInputFile(filename), caption="📦 Автоматический бэкап за 12 часов.")
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
    return df.rename(columns={'id': 'ID Заказа', 'client_name': 'Клиент', 'material': 'Материал', 'start_weight': 'Входной вес (г)', 'gold_rate': 'Курс золота', 'advance': 'Аванс', 'end_weight': 'Чистый вес (г)', 'end_stones_weight': 'Камни (ct)', 'price': 'Стоимость', 'status': 'Статус'}).drop(columns=['telegram_id','start_photo_id','end_photo_id'], errors='ignore')

# --- СОСТОЯНИЯ ---
class NewOrder(StatesGroup): client_name, material, start_weight, gold_rate, advance, start_stones, photo = State(), State(), State(), State(), State(), State(), State()
class CloseOrder(StatesGroup): order_id, end_weight, end_stones_weight, end_stones, price, end_photo = State(), State(), State(), State(), State(), State()
class EditOrder(StatesGroup): order_id, waiting_new_value = State(), State()

# Состояние для ввода имени плательщика
class PaymentState(StatesGroup):
    sender_name = State()

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ Новый заказ"), KeyboardButton(text="🏁 Завершить заказ")], [KeyboardButton(text="✏️ Редактировать"), KeyboardButton(text="📋 Активные заказы")], [KeyboardButton(text="📊 Отчет в Excel"), KeyboardButton(text="💳 Моя подписка")]], resize_keyboard=True)
skip_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏩ Пропустить")]], resize_keyboard=True)
skip_photo_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⏩ Пропустить фото")]], resize_keyboard=True)

# --- МИДЛВАРЬ / ФУНКЦИЯ ФИЛЬТРАЦИИ ОПЛАТЫ ---
async def has_access_or_alert(message: Message) -> bool:
    has_access, status_msg = check_subscription(message.from_user.id)
    if has_access:
        return True
        
    pay_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Инструкция по оплате", callback_data="show_payment_info")],
        [InlineKeyboardButton(text="✅ Я оплатил(а) 100 руб.", callback_data="start_payment_confirmation")]
    ])
    await message.answer(
        f"⛔ <b>Доступ заблокирован!</b>\n\n🔴 Статус: {status_msg}.\n"
        f"Ваш бесплатный пробный период (3 дня) завершился.\n\n"
        f"Для продолжения использования бота и сохранения базы данных требуется продление подписки. "
        f"Стоимость: <b>100 рублей / 30 дней</b>.", reply_markup=pay_btn, parse_mode="HTML"
    )
    return False

# --- ХЕНДЛЕРЫ ---
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id, reg_date) VALUES (?, ?)", (message.from_user.id, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    
    _, status = check_subscription(message.from_user.id)
    await message.answer(f"Привет! Я бот личного учета ювелирных заказов.\n\nℹ️ Ваш статус: <b>{status}</b>", reply_markup=main_kb, parse_mode="HTML")

@dp.message(F.text == "💳 Моя подписка")
async def sub_info(message: Message):
    _, status = check_subscription(message.from_user.id)
    await message.answer(f"📊 <b>Информация о подписке:</b>\n\nСтатус: {status}", parse_mode="HTML")

# --- ИНЛАЙН ХЕНДЛЕРЫ ОПЛАТЫ ---
@dp.callback_query(F.data == "show_payment_info")
async def payment_info(callback: CallbackQuery):
    text = (
        "<b>Реквизиты для оплаты подписки (100 руб / мес):</b>\n\n"
        "Переведите 100 рублей по номеру телефона:\n"
        "<code>+79642480507</code> (Сбербанк / Т-Банк / СБП)\n"
        "Получатель: Администратор бота\n\n"
        "⚠️ После перевода обязательно нажмите кнопку <b>«✅ Я оплатил(а)»</b> ниже, чтобы указать свои данные."
    )
    confirm_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил(а) 100 руб.", callback_data="start_payment_confirmation")]
    ])
    await callback.message.answer(text, reply_markup=confirm_btn, parse_mode="HTML")
    await callback.answer()

# Нажатие на кнопку оплаты -> запуск FSM сценария ввода имени
@dp.callback_query(F.data == "start_payment_confirmation")
async def ask_payment_sender_name(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentState.sender_name)
    await callback.message.answer(
        "✍️ <b>Пожалуйста, введите имя и фамилию отправителя</b> (или комментарий, который вы указали при переводе в банке).\n\n"
        "Это нужно, чтобы администратор понял, от кого пришли деньги, и сразу включил вам бота:"
    )
    await callback.answer()

# Принятие имени плательщика и пересылка админу вместе с ссылкой на профиль
@dp.message(PaymentState.sender_name)
async def process_payment_sender_name(message: Message, state: FSMContext):
    sender_info = message.text
    user = message.from_user
    username_text = f"@{user.username}" if user.username else "не установлен"
    
    admin_confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Продлить на 1 месяц", callback_data=f"activate_sub_{user.id}")]
    ])
    
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 <b>Поступила заявка на оплату подписки!</b>\n\n"
                 f"👤 Имя в ТГ: {user.full_name}\n"
                 f"🔗 Юзернейм: {username_text}\n"
                 f"🆔 ID пользователя: <code>{user.id}</code>\n"
                 f"💳 <b>ОТ КОГО ДЕНЬГИ (указал юзер):</b> {sender_info}\n\n"
                 f"Проверьте поступление 100 рублей на карте. Если деньги пришли, нажмите кнопку ниже:",
            reply_markup=admin_confirm_kb, parse_mode="HTML"
        )
        await message.answer("⏳ Спасибо! Данные отправлены администратору. Доступ включится сразу после подтверждения.", reply_markup=main_kb)
    except Exception as e:
        await message.answer("Произошла ошибка связи с администратором. Попробуйте отправить имя еще раз чуть позже.")
        
    await state.clear()

@dp.callback_query(F.data.startswith("activate_sub_"))
async def admin_activate_sub(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет прав на это действие!", show_alert=True)
        return
        
    target_user_id = int(callback.data.split("_")[2])
    new_pay_till = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET pay_till = ? WHERE telegram_id = ?", (new_pay_till, target_user_id))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(f"✅ Подписка для пользователя {target_user_id} успешно продлена до {new_pay_till}!")
    try:
        await bot.send_message(chat_id=target_user_id, text=f"🎉 <b>Оплата подтверждена!</b>\nВаша подписка успешно продлена на 30 дней (до {new_pay_till}). Спасибо, что пользуетесь ботом!", parse_mode="HTML")
    except: pass
    await callback.answer()

# --- ОСТАЛЬНЫЕ ХЕНДЛЕРЫ С ПРОВЕРКОЙ ДОСТУПА ---
@dp.message(F.text == "📋 Активные заказы")
async def show_active_orders(message: Message):
    if not await has_access_or_alert(message): return
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_name, material, start_weight FROM orders WHERE status = 'В работе' AND telegram_id = ?", (message.from_user.id,))
    orders = cursor.fetchall()
    conn.close()
    if not orders: return await message.answer("У вас сейчас нет активных заказов в работе.")
    response = "<b>Ваши заказы в работе:</b>\n\n"
    for o in orders: response += f"🆔 ID: {o[0]} | 👤 {o[1]} | 📦 {o[2]} | ⚖️ Входной: {o[3]}г\n"
    await message.answer(response, parse_mode="HTML")

@dp.message(F.text == "📊 Отчет в Excel")
async def export_to_excel(message: Message):
    if not await has_access_or_alert(message): return
    await message.answer("🔄 Формирую ваш личный отчет...")
    df_beauty = get_excel_report(user_id=message.from_user.id)
    if df_beauty is None: return await message.answer("Ваша база данных пока пуста.")
    filename = f"Ювелирные_Заказы_{message.from_user.id}.xlsx"
    df_beauty.to_excel(filename, index=False)
    await message.answer_document(document=FSInputFile(filename), caption="📋 Свежий отчет!")
    if os.path.exists(filename): os.remove(filename)

@dp.message(F.text == "➕ Новый заказ")
async def start_new_order(message: Message, state: FSMContext):
    if not await has_access_or_alert(message): return
    await state.set_state(NewOrder.client_name)
    await message.answer("Введите имя или контакты клиента:")

@dp.message(NewOrder.client_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await state.set_state(NewOrder.material)
    await message.answer("Из какого материала заказ?:")

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
        await state.set_state(NewOrder.gold_rate)
        await message.answer("Введите курс золота за грамм на сегодня (руб):", reply_markup=skip_kb)
    except ValueError: await message.answer("Введите вес числом:")

@dp.message(NewOrder.gold_rate)
async def process_gold_rate(message: Message, state: FSMContext):
    rate = 0.0 if message.text == "⏩ Пропустить" else float(message.text.replace(',', '.').replace(' ', ''))
    await state.update_data(gold_rate=rate)
    await state.set_state(NewOrder.advance)
    await message.answer("Введите сумму внесенного аванса (если нет — 0):", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="0")]], resize_keyboard=True))

@dp.message(NewOrder.advance)
async def process_advance(message: Message, state: FSMContext):
    try:
        advance = float(message.text.replace(',', '.').replace(' ', ''))
        await state.update_data(advance=advance)
        await state.set_state(NewOrder.start_stones)
        await message.answer("Какие камни планируются?:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Нет")]], resize_keyboard=True))
    except ValueError: await message.answer("Введите сумму аванса числом:")

@dp.message(NewOrder.start_stones)
async def process_start_stones(message: Message, state: FSMContext):
    await state.update_data(start_stones=message.text)
    await state.set_state(NewOrder.photo)
    await message.answer("Отправьте фото или пропустите:", reply_markup=skip_photo_kb)

@dp.message(NewOrder.photo, F.photo)
async def process_photo(message: Message, state: FSMContext): await save_order_to_db(message.photo[-1].file_id, message, state)
@dp.message(NewOrder.photo, F.text == "⏩ Пропустить фото")
async def process_skip_photo(message: Message, state: FSMContext): await save_order_to_db(None, message, state)

async def save_order_to_db(photo_id, message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect('jewelry_orders.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO orders (telegram_id, client_name, material, start_weight, start_stones, start_photo_id, gold_rate, advance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (message.from_user.id, data['client_name'], data['material'], data['start_weight'], data['start_stones'], photo_id, data['gold_rate'], data['advance']))
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    caption = f"✅ Заказ №{order_id} создан!\n👤 Клиент: {data['client_name']}\n⚖️ Входной вес: {data['start_weight']} г"
    await (message.answer_photo(photo=photo_id, caption=caption, reply_markup=main_kb) if photo_id else message.answer(caption, reply_markup=main_kb))
    await state.clear()

@dp.message(F.text == "✏️ Редактировать")
async def start_edit_order(message: Message, state: FSMContext):
    if not await has_access_or_alert(message): return
    await state.set_state(EditOrder.order_id)
    await message.answer("Введите ID заказа для изменения:")

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
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👤 Имя клиента", callback_data="edit_client_name")],[InlineKeyboardButton(text="📦 Материал", callback_data="edit_material")],[InlineKeyboardButton(text="⚖️ Входной вес", callback_data="edit_start_weight")]])
            await message.answer(f"Выбран заказ №{order[0]}. Что изменить?", reply_markup=inline_kb)
        else: await message.answer("Заказ не найден.")
    except ValueError: await message.answer("ID числом:")

@dp.callback_query(F.data.startswith("edit_"))
async def process_edit_choice(callback: CallbackQuery, state: FSMContext):
    field = callback.data.split("_", 1)[1]
    await state.update_data(edit_field=field)
    await state.set_state(EditOrder.waiting_new_value)
    await callback.message.answer(f"Ожидаю новое значение:")
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
    await message.answer("✅ Успешно обновлено!", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "🏁 Завершить заказ")
async def start_close_order(message: Message, state: FSMContext):
    if not await has_access_or_alert(message): return
    await state.set_state(CloseOrder.order_id)
    await message.answer("Введите ID заказа для завершения:")

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
            await message.answer(f"Заказ №{order_id}. Введите вес ТОЛЬКО металла (г):")
        else: await message.answer("Заказ не найден.")
    except ValueError: await message.answer("ID числом:")

@dp.message(CloseOrder.end_weight)
async def process_end_weight(message: Message, state: FSMContext):
    await state.update_data(end_weight=float(message.text.replace(',', '.')))
    await state.set_state(CloseOrder.end_stones_weight)
    await message.answer("Введите вес камней в граммах (бот переведет в ct):")

@dp.message(CloseOrder.end_stones_weight)
async def process_end_stones_weight(message: Message, state: FSMContext):
    await state.update_data(end_stones_weight=round(float(message.text.replace(',', '.')) * 5.0, 3))
    await state.set_state(CloseOrder.end_stones)
    await message.answer("Какие камни закрепили?:")

@dp.message(CloseOrder.end_stones)
async def process_end_stones(message: Message, state: FSMContext):
    await state.update_data(end_stones=message.text)
    await state.set_state(CloseOrder.price)
    await message.answer("Итоговая стоимость заказа:")

@dp.message(CloseOrder.price)
async def process_price(message: Message, state: FSMContext):
    await state.update_data(price=float(message.text.replace(',', '.').replace(' ', '')))
    await state.set_state(CloseOrder.end_photo)
    await message.answer("Отправьте фото готового изделия или пропустите:", reply_markup=skip_photo_kb)

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
    
    caption = f"🎉 Заказ №{data['order_id']} закрыт!\n\n📉 Потери: {loss} г\n📊 Итог: {total_metal} г\n💰 Осталось оплатить: {to_pay} руб."
    await (message.answer_photo(photo=end_photo_id, caption=caption, reply_markup=main_kb) if end_photo_id else message.answer(caption, reply_markup=main_kb))
    await state.clear()

# --- ЗАПУСК ---
async def main():
    init_db()
    await start_background_web_server()
    asyncio.create_task(backup_scheduler())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
