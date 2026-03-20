import asyncio
import logging
import xml.etree.ElementTree as ET
import re
import aiohttp
import sqlite3
from google import genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import sys

# ==========================================
# НАСТРОЙКИ 
# ==========================================
TELEGRAM_BOT_TOKEN = "8732409277:AAGEYg8ptrWGygY-EmB23rcm93gFLtWE5AU"
TELEGRAM_USER_ID = 1652878568
GEMINI_API_KEY = "AIzaSyB5SmtomV2Pbs6vKCwzchaXdJy4-CkB6Sk"

# Ключевые слова для поиска
KEYWORDS = ["python", "telegram", "телеграм", "парсер", "api", "скрипт", "чат-бот", "бот", "openai", "chatgpt"]

# Источники RSS-лент (Оставили только стабильно работающий FL.ru)
RSS_FEEDS = {
    "FL": "https://www.fl.ru/rss/all.xml"
}

# Интервал автоматической проверки
CHECK_INTERVAL = 300  

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

force_scan_event = asyncio.Event()
compiled_keywords = [re.compile(rf'\b{re.escape(k)}\b', re.IGNORECASE) for k in KEYWORDS]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

# ==========================================
# БАЗА ДАННЫХ (SQLITE)
# ==========================================
def init_db():
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
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM seen_jobs WHERE id = ?', (job_id,))
    result = cursor.fetchone()
    conn.close()
    return bool(result)

def mark_job_seen(job_id: str):
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO seen_jobs (id) VALUES (?)', (job_id,))
    conn.commit()
    conn.close()

def get_total_seen_jobs() -> int:
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
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Искать прямо сейчас", callback_data="force_scan")]
        ])
        await message.answer("👋 <b>Привет!</b> Скайнет-сканер работает.\n\nЖми /status для статистики.", reply_markup=kb)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id == TELEGRAM_USER_ID:
        total_jobs = get_total_seen_jobs()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Искать прямо сейчас", callback_data="force_scan")]
        ])
        
        sources_list = ", ".join(RSS_FEEDS.keys())
        await message.answer(
            f"✅ <b>Система стабильна.</b>\n"
            f"🌐 <b>Активные источники ({len(RSS_FEEDS)}):</b> {sources_list}\n"
            f"🧠 <b>AI-Фильтр:</b> Включен (отсев неадеквата)\n"
            f"🔍 <b>Ключевых слов:</b> {len(KEYWORDS)}\n"
            f"🗂 <b>Заказов в базе (SQLite):</b> {total_jobs}\n"
            f"⏱ <b>Интервал проверки:</b> каждые {CHECK_INTERVAL // 60} минут.",
            reply_markup=kb
        )

@dp.callback_query(F.data == "force_scan")
async def process_force_scan(callback: CallbackQuery):
    if callback.from_user.id == TELEGRAM_USER_ID:
        await callback.answer("Запускаю внеочередное сканирование...", show_alert=False)
        force_scan_event.set() 

# ==========================================
# ЛОГИКА ИИ (ФИЛЬТРАЦИЯ + ГЕНЕРАЦИЯ ОТКЛИКА)
# ==========================================
async def generate_cover_letter(title: str, description: str) -> str:
    prompt = f"""
    Ты — профессиональный Python-разработчик на фрилансе. Тебе поступил заказ.
    
    Заказ: {title}
    Описание: {description}
    
    ШАГ 1: Оцени адекватность заказа. Если заказчик просит сделать что-то нереально огромное за копейки (например, клон соцсети за 2000 рублей), или это откровенный спам/скам, или тестовое задание без оплаты — напиши в ответе РОВНО ОДНО СЛОВО: SKIP
    
    ШАГ 2: Если заказ в целом адекватный, напиши короткий, уверенный и цепляющий отклик от первого лица на русском языке. 
    Без воды, без лишних приветствий (сразу к делу). Упомяни, что готов приступить и имеешь нужный опыт. Максимум 4-5 предложений.
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
        return "⚠️ Ошибка генерации."

# ==========================================
# ПАРСИНГ ИСТОЧНИКОВ
# ==========================================
async def fetch_rss_feed(session: aiohttp.ClientSession, source_name: str, url: str) -> list:
    """Универсальный асинхронный парсер для любой RSS-ленты."""
    jobs = []
    try:
        async with session.get(url, headers=HEADERS, timeout=15) as response:
            if response.status != 200:
                logging.error(f"Ошибка парсинга {source_name}: Статус {response.status}")
                return jobs
            
            content = await response.text()
            
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                logging.error(f"Ошибка чтения XML с биржи {source_name}.")
                return jobs

            for item in root.findall('.//item'):
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                description = item.find('description').text if item.find('description') is not None else ""
                
                description = re.sub(r'<[^>]+>', '', description)
                description = description.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
                
                jobs.append({
                    "id": link, 
                    "title": f"[{source_name}] {title.strip()}", 
                    "link": link.strip(), 
                    "description": description.strip()
                })
    except Exception as e:
        logging.error(f"Сбой при подключении к {source_name}: {e}")
        
    return jobs

# ==========================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ
# ==========================================
def contains_keywords(title: str) -> bool:
    return any(pattern.search(title) for pattern in compiled_keywords)

async def scan_freelance_boards():
    try:
        await bot.send_message(TELEGRAM_USER_ID, "🚀 <b>Скайнет v7.1 запущен!</b>\nМертвые RSS-ленты удалены. Мониторинг FL.ru активен.")
    except Exception as e:
        logging.error(f"Ошибка TG: {e}")
        
    async with aiohttp.ClientSession() as session:
        while True:
            logging.info("Сканирую активные ленты...")
            force_scan_event.clear() 
            
            tasks = [fetch_rss_feed(session, name, url) for name, url in RSS_FEEDS.items()]
            results = await asyncio.gather(*tasks)
            
            all_jobs = []
            for job_list in results:
                all_jobs.extend(job_list)
            
            new_matches = 0
            for job in all_jobs:
                if is_job_seen(job['id']):
                    continue
                
                mark_job_seen(job['id'])
                
                if contains_keywords(job['title']):
                    
                    cover_letter = await generate_cover_letter(job['title'], job['description'])
                    
                    if "SKIP" in cover_letter.upper():
                        logging.info(f"🚫 Нейросеть забраковала неадекватный заказ: {job['title']}")
                        continue
                    
                    new_matches += 1
                    logging.info(f"Нашел отличный заказ: {job['title']}")
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔗 Открыть заказ на бирже", url=job['link'])]
                    ])
                    
                    msg = (
                        f"🔥 <b>Новый заказ!</b>\n\n"
                        f"<b>Название:</b> {job['title']}\n\n"
                        f"🤖 <b>Сгенерированный отклик:</b>\n"
                        f"<code>{cover_letter}</code>"
                    )
                    
                    try:
                        await bot.send_message(TELEGRAM_USER_ID, msg, reply_markup=kb)
                    except Exception as e:
                        logging.error(f"Ошибка отправки: {e}")
            
            logging.info(f"Проверка завершена. Найдено {new_matches} целевых заказов. Жду {CHECK_INTERVAL} сек или ручного запуска.")
            
            try:
                await asyncio.wait_for(force_scan_event.wait(), timeout=CHECK_INTERVAL)
                logging.info("⚡ Запущен принудительный поиск по кнопке!")
            except asyncio.TimeoutError:
                pass 

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
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