import asyncio
import logging
import xml.etree.ElementTree as ET
import re
import aiohttp
import sqlite3
from google import genai
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import sys

# ==========================================
# НАСТРОЙКИ 
# ==========================================
TELEGRAM_BOT_TOKEN = "8732409277:AAGEYg8ptrWGygY-EmB23rcm93gFLtWE5AU"
TELEGRAM_USER_ID = 1652878568
GEMINI_API_KEY = "AIzaSyB5SmtomV2Pbs6vKCwzchaXdJy4-CkB6Sk"

# Ключевые слова для поиска
KEYWORDS = ["python", "telegram", "телеграм", "парсер", "api", "скрипт", "чат-бот", "бот", "openai", "chatgpt"]

# Интервал проверки новых заказов
CHECK_INTERVAL = 300  

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Предкомпилируем регулярные выражения для скорости
compiled_keywords = [re.compile(rf'\b{re.escape(k)}\b', re.IGNORECASE) for k in KEYWORDS]

# ==========================================
# БАЗА ДАННЫХ (SQLITE)
# ==========================================
def init_db():
    """Создает таблицу, если её еще нет."""
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_jobs (
            id TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def is_job_seen(job_id: str) -> bool:
    """Проверяет, есть ли заказ в базе."""
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM seen_jobs WHERE id = ?', (job_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

def mark_job_seen(job_id: str):
    """Добавляет заказ в базу просмотренных."""
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO seen_jobs (id) VALUES (?)', (job_id,))
    conn.commit()
    conn.close()

def get_total_seen_jobs() -> int:
    """Возвращает общее количество заказов в базе."""
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM seen_jobs')
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ==========================================
# ТЕЛЕГРАМ КОМАНДЫ (УПРАВЛЕНИЕ)
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == TELEGRAM_USER_ID:
        await message.answer("👋 <b>Привет!</b> Скайнет-сканер работает.\n\nЖми /status, чтобы проверить статистику базы данных.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id == TELEGRAM_USER_ID:
        total_jobs = get_total_seen_jobs()
        await message.answer(
            f"✅ <b>Система стабильна.</b>\n"
            f"🔍 <b>Ключевых слов:</b> {len(KEYWORDS)}\n"
            f"🗂 <b>Заказов в базе (SQLite):</b> {total_jobs}\n"
            f"⏱ <b>Интервал проверки:</b> каждые {CHECK_INTERVAL // 60} минут."
        )

# ==========================================
# ЛОГИКА ИИ (ГЕНЕРАЦИЯ ОТКЛИКА)
# ==========================================
async def generate_cover_letter(title: str, description: str) -> str:
    prompt = f"""
    Ты — профессиональный Python-разработчик на фрилансе. 
    Твоя задача — написать короткий, уверенный и цепляющий отклик на заказ. 
    Без воды, без лишних приветствий (сразу к делу). Упомяни, что готов приступить и имеешь нужный опыт.
    
    Заказ: {title}
    Описание: {description}
    
    Напиши отклик от первого лица на русском языке. Максимум 4-5 предложений.
    """
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        ))
        await asyncio.sleep(4) 
        return response.text.strip()
    except Exception as e:
        logging.error(f"Ошибка API Gemini: {e}")
        return "⚠️ Ошибка генерации. Возможно, лимит запросов."

# ==========================================
# ПАРСИНГ (СБОР ДАННЫХ)
# ==========================================
async def fetch_fl_jobs(session: aiohttp.ClientSession):
    url = "https://www.fl.ru/rss/all.xml"
    jobs = []
    try:
        async with session.get(url, timeout=10) as response:
            if response.status != 200:
                logging.error(f"Ошибка FL.ru: {response.status}")
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
        logging.error(f"Ошибка парсинга: {e}")
    
    return jobs

# ==========================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ
# ==========================================
def contains_keywords(title: str) -> bool:
    return any(pattern.search(title) for pattern in compiled_keywords)

async def scan_freelance_boards():
    try:
        await bot.send_message(TELEGRAM_USER_ID, "🚀 <b>Скайнет запущен!</b> База данных подключена. Мониторинг активен.")
    except Exception as e:
        logging.error(f"Ошибка TG: {e}")
        
    async with aiohttp.ClientSession() as session:
        while True:
            logging.info("Сканирую ленту...")
            jobs = await fetch_fl_jobs(session)
            
            new_matches = 0
            for job in jobs:
                # Проверяем в базе SQLite
                if is_job_seen(job['id']):
                    continue
                
                # Сразу записываем в базу
                mark_job_seen(job['id'])
                
                if contains_keywords(job['title']):
                    new_matches += 1
                    logging.info(f"Нашел заказ: {job['title']}")
                    
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
                        logging.error(f"Ошибка отправки: {e}")
            
            logging.info(f"Найдено {new_matches} новых заказов. Сплю {CHECK_INTERVAL} сек.")
            await asyncio.sleep(CHECK_INTERVAL)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    # Инициализируем базу данных при старте
    init_db()
    
    asyncio.create_task(scan_freelance_boards())
    await dp.start_polling(bot)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановлено.")