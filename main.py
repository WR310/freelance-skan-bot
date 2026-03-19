import asyncio
import logging
import xml.etree.ElementTree as ET
import aiohttp
from google import genai
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

# Ключевые слова для поиска заказов (теперь ищем только в заголовках)
KEYWORDS = ["python", "бот", "telegram", "телеграм", "парсер", "api", "скрипт", "автоматизация", "chatgpt"]

# Интервал проверки новых заказов (в секундах)
CHECK_INTERVAL = 300  

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация нового клиента Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Настройка Telegram бота
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Хранилище уже просмотренных заказов (ID), чтобы не спамить дублями
seen_jobs = set()

# ==========================================
# ЛОГИКА ИИ (ГЕНЕРАЦИЯ ОТКЛИКА)
# ==========================================
async def generate_cover_letter(title: str, description: str) -> str:
    """Генерирует продающий отклик с помощью нового SDK Google GenAI."""
    prompt = f"""
    Ты — профессиональный Python-разработчик на фрилансе. 
    Твоя задача — написать короткий, уверенный и цепляющий отклик на заказ. 
    Без воды, без лишних приветствий (сразу к делу). Упомяни, что готов приступить и имеешь нужный опыт.
    
    Заказ: {title}
    Описание: {description}
    
    Напиши отклик от первого лица на русском языке. Максимум 4-5 предложений.
    """
    try:
        # Для нового SDK используем run_in_executor, так как асинхронный клиент пока может быть нестабилен
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: client.models.generate_content(
            model='gemini-2.5-flash', # Используем актуальную модель
            contents=prompt
        ))
        return response.text.strip()
    except Exception as e:
        logging.error(f"Ошибка при генерации отклика: {e}")
        return "⚠️ Ошибка генерации текста отклика."

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
                
                description = description.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
                
                jobs.append({
                    "id": link,  
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
def contains_keywords(title: str) -> bool:
    """Проверяет, есть ли ключевые слова СТРОГО В ЗАГОЛОВКЕ."""
    text_lower = title.lower()
    return any(keyword in text_lower for keyword in KEYWORDS)

async def scan_freelance_boards():
    """Фоновая задача сканирования бирж."""
    try:
        await bot.send_message(TELEGRAM_USER_ID, "🚀 <b>Скайнет запущен (v2.0)!</b> Мониторинг настроен...")
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение. Ошибка: {e}")
        
    async with aiohttp.ClientSession() as session:
        while True:
            logging.info("Начинаю проверку новых заказов...")
            jobs = await fetch_fl_jobs(session)
            
            new_matches = 0
            for job in jobs:
                if job['id'] in seen_jobs:
                    continue
                
                seen_jobs.add(job['id'])
                
                # ИЩЕМ КЛЮЧИ ТОЛЬКО В ЗАГОЛОВКЕ
                if contains_keywords(job['title']):
                    new_matches += 1
                    logging.info(f"Найден подходящий заказ: {job['title']}")
                    
                    cover_letter = await generate_cover_letter(job['title'], job['description'])
                    
                    msg = (
                        f"🔥 <b>Новый заказ!</b>\n\n"
                        f"<b>Название:</b> {job['title']}\n"
                        f"<b>Ссылка:</b> {job['link']}\n\n"
                        f"🤖 <b>Сгенерированный отклик:</b>\n"
                        f"<code>{cover_letter}</code>"
                    )
                    
                    try:
                        await bot.send_message(TELEGRAM_USER_ID, msg)
                    except Exception as e:
                        logging.error(f"Ошибка отправки в Telegram: {e}")
            
            logging.info(f"Проверка завершена. Найдено {new_matches} целевых заказов. Сплю {CHECK_INTERVAL} сек.")
            await asyncio.sleep(CHECK_INTERVAL)

# ==========================================
# ЗАПУСК ПРОГРАММЫ
# ==========================================
async def main():
    asyncio.create_task(scan_freelance_boards())
    await dp.start_polling(bot)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен вручную (Ctrl+C).")