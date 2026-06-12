import os
import asyncio
import pandas as pd
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiohttp import web

# ==========================================
# 1. НАСТРОЙКИ (ПОДСТАВЬ СВОЙ ТОКЕН)
# ==========================================
BOT_TOKEN = "ТВОЙ_ТОКЕН_БОТА_СЮДА"
ADMIN_ID = 123456789  # Твой личный Telegram ID для получения автобэкапов
EXCEL_FILE = "jewellery_database.xlsx"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==========================================
# 2. МИКРО-СЕРВЕР ДЛЯ ОБМАНА RENDER (WEB SERVICE)
# ==========================================
async def handle(request):
    return web.Response(text="Бот ювелирной мастерской успешно запущен и работает!")

async def start_background_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Подхватываем порт, который требует Render
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# ==========================================
# 3. КОМАНДЫ БОТА
# ==========================================

# Приветствие при команде /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот ювелирной мастерской.\n\n"
        "📊 Чтобы получить список только СВОИХ заказов в формате Excel, "
        "напиши команду: /myorders"
    )

# Выгрузка заказов конкретного человека
@dp.message(Command("myorders"))
async def cmd_my_orders(message: types.Message):
    # Получаем имя или юзернейм того, кто нажал команду
    user_name = message.from_user.full_name
    user_username = message.from_user.username
    
    await message.answer(f"🔍 Ищу заказы для пользователя: {user_name}...")

    # Проверяем, существует ли вообще база данных
    if not os.path.exists(EXCEL_FILE):
        await message.answer("❌ База данных еще не создана. Заказов нет.")
        return

    try:
        # Читаем общую таблицу
        df = pd.read_excel(EXCEL_FILE)

        # Проверяем, есть ли колонка с мастером/клиентом. 
        # ЗАМЕНЯЙ 'Мастер' на точное название твоей колонки в Excel!
        if 'Мастер' not in df.columns:
            await message.answer("❌ В таблице не найдена колонка 'Мастер'. Обратись к администратору.")
            return

        # Фильтруем: ищем совпадения по Full Name или по Username
        filtered_df = df[
            (df['Мастер'].astype(str).str.lower() == user_name.lower()) | 
            (df['Мастер'].astype(str).str.lower() == f"@{str(user_username).lower()}")
        ]

        # Если ничего не нашли
        if filtered_df.empty:
            await message.answer(f"🤷‍♂️ У меня нет зарегистрированных заказов на имя {user_name}.")
            return

        # Создаем временный файл только с его заказами
        user_file = f"orders_{message.from_user.id}.xlsx"
        filtered_df.to_excel(user_file, index=False)

        # Отправляем файл пользователю
        document = FSInputFile(user_file)
        await message.answer_document(
            document, 
            caption=f"📋 Вот список ваших актуальных заказов, {user_name}!"
        )

        # Удаляем временный файл с сервера, чтобы не забивать место
        os.remove(user_file)

    except Exception as e:
        await message.answer(f"💥 Ошибка при обработке файла: {e}")

# ==========================================
# 4. СИСТЕМА АВТОМАТИЧЕСКИХ БЭКАПОВ
# ==========================================
async def auto_backup_task():
    while True:
        # Каждые 24 часа (86400 секунд) бот будет слать тебе копию базы
        await asyncio.sleep(86400) 
        if os.path.exists(EXCEL_FILE):
            try:
                current_time = datetime.now().strftime("%Y-%m-%d_%H-%M")
                backup_doc = FSInputFile(EXCEL_FILE)
                await bot.send_document(
                    chat_id=ADMIN_ID, 
                    document=backup_doc, 
                    caption=f"📦 Автобэкап базы данных от {current_time}"
                )
            except Exception as e:
                print(f"Ошибка бэкапа: {e}")

# ==========================================
# 5. ГЛАВНЫЙ ЗАПУСК
# ==========================================
async def main():
    # 1. Запускаем обманку для Render
    await start_background_web_server()
    
    # 2. Запускаем фоновую задачу бэкапов
    asyncio.create_task(auto_backup_task())
    
    print("🚀 Бот запущен, веб-сервер для Render поднят!")
    
    # 3. Включаем чтение сообщений Telegram
    await dp.start_polling(bot)

if name == "main":
    asyncio.run(main())
