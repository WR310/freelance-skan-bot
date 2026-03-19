import asyncio
import logging
import xml.etree.ElementTree as ET
import aiohttp
import google.generativeai as genai
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import sys

# ==========================================
# НАСТРОЙКИ 
# ==========================================
TELEGRAM_BOT_TOKEN = "8732409277:AAGEYg8ptrWGygY-EmB23rcm93gFLtWE5AU"
TELEGRAM_USER_ID = 1652878568
GEMINI_API_KEY = "AIzaSyB5SmtomV2Pbs6vKCwzchaXdJy4-CkB6Sk"

# Ключевые слова для поиска заказов
KEYWORDS = ["python", "бот", "telegram", "телеграм", "парсер", "api", "скрипт", "автоматизация", "chatgpt"]

# Интервал проверки новых заказов (в секундах)
CHECK_INTERVAL = 300  # 5 минут (не стоит делать меньше, чтобы FL.ru не забанил IP)

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# Настройка Telegram бота
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Хранилище уже просмотренных заказов (ID), чтобы не спамить дублями
seen_jobs = set()

# ==========================================
# ЛОГИКА ИИ (ГЕНЕРАЦИЯ ОТКЛИКА)
# ==========================================
async def generate_cover_letter(title: str, description: str) -> str:
    """Генерирует продающий отклик с помощью Gemini (Нативная асинхронность)."""
    prompt = f"""
    Ты — профессиональный Python-разработчик на фрилансе. 
    Твоя задача — написать короткий, уверенный и цепляющий отклик на заказ. 
    Без воды, без лишних приветствий (сразу к делу). Упомяни, что готов приступить и имеешь нужный опыт.
    
    Заказ: {title}
    Описание: {description}
    
    Напиши отклик от первого лица на русском языке. Максимум 4-5 предложений.
    """
    try:
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Ошибка при генерации отклика: {e}")
        return "⚠️ Ошибка генерации текста отклика. Проверь API-ключ Gemini."

# ==========================================
# ПАРСИНГ (СБОР ДАННЫХ)
# ==========================================
async def fetch_fl_jobs(session: aiohttp.ClientSession):
    """Парсит открытую RSS-ленту FL.ru."""
    url = "https://www.fl.ru/rss/all.xml"
    jobs = []
    try:
        async with session.get(url, timeout=10) as response:
            if response.status != 200:
                logging.error(f"Ошибка доступа к FL.ru: Статус {response.status}")
                return jobs
            
            content = await response.text()
            root = ET.fromstring(content)
            
            for item in root.findall('./channel/item'):
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                description = item.find('description').text if item.find('description') is not None else ""
                
                # Очистка описания от базовых HTML сущностей
                description = description.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
                
                jobs.append({
                    "id": link,  # В качестве уникального ID используем ссылку
                    "title": title,
                    "link": link,
                    "description": description
                })
    except Exception as e:
        logging.error(f"Ошибка парсинга FL.ru: {e}")
    
    return jobs

# ==========================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ
# ==========================================
def contains_keywords(text: str) -> bool:
    """Проверяет, есть ли ключевые слова в тексте."""
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in KEYWORDS)

async def scan_freelance_boards():
    """Фоновая задача сканирования бирж."""
    try:
        await bot.send_message(TELEGRAM_USER_ID, "🚀 <b>Скайнет запущен!</b> Начинаю мониторинг заказов...")
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение. ВАЖНО: Зайди в свой бот и нажми /start! Ошибка: {e}")
        
    async with aiohttp.ClientSession() as session:
        while True:
            logging.info("Начинаю проверку новых заказов...")
            jobs = await fetch_fl_jobs(session)
            
            new_matches = 0
            for job in jobs:
                if job['id'] in seen_jobs:
                    continue
                
                seen_jobs.add(job['id'])
                
                # Проверяем на ключи и заголовок, и описание
                full_text = f"{job['title']} {job['description']}"
                if contains_keywords(full_text):
                    new_matches += 1
                    logging.info(f"Найден подходящий заказ: {job['title']}")
                    
                    # Генерируем отклик через ИИ
                    cover_letter = await generate_cover_letter(job['title'], job['description'])
                    
                    # Формируем сообщение
                    msg = (
                        f"🔥 <b>Новый заказ!</b>\n\n"
                        f"<b>Название:</b> {job['title']}\n"
                        f"<b>Ссылка:</b> {job['link']}\n\n"
                        f"🤖 <b>Сгенерированный отклик (нажми, чтобы скопировать):</b>\n"
                        f"<code>{cover_letter}</code>"
                    )
                    
                    try:
                        await bot.send_message(TELEGRAM_USER_ID, msg)
                    except Exception as e:
                        logging.error(f"Ошибка отправки в Telegram: {e}")
            
            logging.info(f"Проверка завершена. Найдено {new_matches} новых заказов по ключам. Сплю {CHECK_INTERVAL} сек.")
            await asyncio.sleep(CHECK_INTERVAL)

# ==========================================
# ЗАПУСК ПРОГРАММЫ
# ==========================================
async def main():
    # Запускаем фоновый парсинг как отдельную асинхронную задачу
    asyncio.create_task(scan_freelance_boards())
    
    # Запускаем поллинг бота (ожидание апдейтов от серверов Telegram)
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Фикс для корректного закрытия Event Loop на Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную (Ctrl+C).")