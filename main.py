import asyncio
import logging
import xml.etree.ElementTree as ET
import re
import aiohttp
import sqlite3
import os
from datetime import datetime
from openpyxl import Workbook, load_workbook
from google import genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from playwright.async_api import async_playwright
import sys

# ==========================================
# НАСТРОЙКИ 
# ==========================================
TELEGRAM_BOT_TOKEN = "8732409277:AAGEYg8ptrWGygY-EmB23rcm93gFLtWE5AU"
TELEGRAM_USER_ID = 1652878568
GEMINI_API_KEY = "AIzaSyB5SmtomV2Pbs6vKCwzchaXdJy4-CkB6Sk"

# Базовые ключи (будут добавлены в БД при первом запуске)
DEFAULT_KEYWORDS = ["python", "telegram", "телеграм", "парсер", "api", "скрипт", "чат-бот", "бот", "openai", "chatgpt"]

RSS_FEEDS = {
    "FL": "https://www.fl.ru/rss/all.xml"
}

CHECK_INTERVAL = 300  

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

force_scan_event = asyncio.Event()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}

# ==========================================
# БАЗА ДАННЫХ (SQLITE)
# ==========================================
def init_db():
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS seen_jobs (id TEXT PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS keywords (word TEXT PRIMARY KEY)''')
    
    # Загружаем базовые ключи, если таблица пустая
    cursor.execute('SELECT COUNT(*) FROM keywords')
    if cursor.fetchone()[0] == 0:
        for k in DEFAULT_KEYWORDS:
            cursor.execute('INSERT OR IGNORE INTO keywords (word) VALUES (?)', (k.lower(),))
            
    conn.commit()
    conn.close()

# Функции работы с заказами
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

# Функции работы с ключами
def get_keywords() -> list:
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('SELECT word FROM keywords')
    words = [row[0] for row in cursor.fetchall()]
    conn.close()
    return words

def add_keyword(word: str) -> bool:
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO keywords (word) VALUES (?)', (word.lower(),))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    conn.close()
    return success

def remove_keyword(word: str) -> bool:
    conn = sqlite3.connect('scanner.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM keywords WHERE word = ?', (word.lower(),))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

# ==========================================
# EXCEL CRM СИСТЕМА
# ==========================================
def save_to_excel(title: str, link: str, cover_letter: str):
    file_name = "clients.xlsx"
    try:
        if not os.path.exists(file_name):
            wb = Workbook()
            ws = wb.active
            ws.title = "Лиды с бирж"
            ws.append(["Дата", "Название заказа", "Ссылка", "Сгенерированный отклик", "Статус (в работе/отказ/игнор)"])
            ws.column_dimensions['A'].width = 15
            ws.column_dimensions['B'].width = 40
            ws.column_dimensions['C'].width = 30
            ws.column_dimensions['D'].width = 60
            ws.column_dimensions['E'].width = 20
        else:
            wb = load_workbook(file_name)
            ws = wb.active

        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        ws.append([date_str, title, link, cover_letter, ""])
        wb.save(file_name)
        logging.info(f"Excel: Заказ '{title[:20]}...' успешно добавлен в CRM.")
    except Exception as e:
        logging.error(f"Ошибка сохранения в Excel: {e}")

# ==========================================
# ТЕЛЕГРАМ КОМАНДЫ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == TELEGRAM_USER_ID:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Искать прямо сейчас", callback_data="force_scan")]
        ])
        await message.answer(
            "👋 <b>Привет!</b> Скайнет-сканер работает.\n\n"
            "Команды:\n"
            "/status — Статистика\n"
            "/keys — Управление ключами", 
            reply_markup=kb
        )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id == TELEGRAM_USER_ID:
        total_jobs = get_total_seen_jobs()
        total_keys = len(get_keywords())
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Искать прямо сейчас", callback_data="force_scan")]
        ])
        
        await message.answer(
            f"✅ <b>Система стабильна.</b>\n"
            f"🌐 <b>Источники:</b> FL.ru (RSS), Kwork (Playwright)\n"
            f"📊 <b>CRM:</b> Активна (clients.xlsx)\n"
            f"🧠 <b>AI-Фильтр:</b> Включен\n"
            f"🔍 <b>Ключевых слов:</b> {total_keys}\n"
            f"🗂 <b>Заказов в базе:</b> {total_jobs}\n"
            f"⏱ <b>Интервал проверки:</b> каждые {CHECK_INTERVAL // 60} минут.",
            reply_markup=kb
        )

@dp.message(Command("keys"))
async def cmd_keys(message: types.Message):
    if message.from_user.id == TELEGRAM_USER_ID:
        keys = get_keywords()
        keys_text = "\n".join([f"• <code>{k}</code>" for k in keys])
        await message.answer(
            f"🔑 <b>Твои ключевые слова для поиска:</b>\n\n"
            f"{keys_text}\n\n"
            f"➕ <b>Добавить:</b>\n<code>/add_key слово</code>\n"
            f"➖ <b>Удалить:</b>\n<code>/del_key слово</code>"
        )

@dp.message(Command("add_key"))
async def cmd_add_key(message: types.Message, command: CommandObject):
    if message.from_user.id == TELEGRAM_USER_ID:
        if not command.args:
            await message.answer("⚠️ Напиши слово после команды. Пример:\n<code>/add_key django</code>")
            return
        word = command.args.strip().lower()
        if add_keyword(word):
            await message.answer(f"✅ Слово <b>{word}</b> добавлено в базу. Оно будет учитываться при следующем сканировании.")
        else:
            await message.answer(f"⚠️ Слово <b>{word}</b> уже есть в базе.")

@dp.message(Command("del_key"))
async def cmd_del_key(message: types.Message, command: CommandObject):
    if message.from_user.id == TELEGRAM_USER_ID:
        if not command.args:
            await message.answer("⚠️ Напиши слово после команды. Пример:\n<code>/del_key парсер</code>")
            return
        word = command.args.strip().lower()
        if remove_keyword(word):
            await message.answer(f"🗑 Слово <b>{word}</b> удалено из поиска.")
        else:
            await message.answer(f"⚠️ Слова <b>{word}</b> нет в базе.")

@dp.callback_query(F.data == "force_scan")
async def process_force_scan(callback: CallbackQuery):
    if callback.from_user.id == TELEGRAM_USER_ID:
        await callback.answer("Запускаю сканирование...", show_alert=False)
        force_scan_event.set() 

# ==========================================
# ЛОГИКА ИИ
# ==========================================
async def generate_cover_letter(title: str, description: str) -> str:
    prompt = f"""
    Ты — профессиональный Python-разработчик на фрилансе. Тебе поступил заказ.
    Заказ: {title}
    Описание: {description}
    ШАГ 1: Оцени адекватность заказа. Если заказчик просит сделать что-то нереально огромное за копейки, или это откровенный спам/скам, или тестовое задание без оплаты — напиши в ответе РОВНО ОДНО СЛОВО: SKIP
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
    jobs = []
    try:
        async with session.get(url, headers=HEADERS, timeout=15) as response:
            if response.status != 200:
                return jobs
            content = await response.text()
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                return jobs
            for item in root.findall('.//item'):
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                description = item.find('description').text if item.find('description') is not None else ""
                description = re.sub(r'<[^>]+>', '', description)
                description = description.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
                jobs.append({"id": link, "title": f"[{source_name}] {title.strip()}", "link": link.strip(), "description": description.strip()})
    except Exception as e:
        logging.error(f"Ошибка RSS {source_name}: {e}")
    return jobs

async def fetch_kwork_jobs() -> list:
    jobs = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
            page = await browser.new_page()
            
            logging.info("Playwright: Открываю Kwork...")
            await page.goto("https://kwork.ru/projects?c=11", timeout=60000)
            await page.wait_for_selector('.want-card', timeout=15000)
            
            cards = await page.query_selector_all('.want-card')
            for card in cards[:15]:
                title_el = await card.query_selector('.wants-card__header-title a')
                desc_el = await card.query_selector('.wants-card__description-text')
                
                if title_el and desc_el:
                    title = await title_el.inner_text()
                    link = await title_el.get_attribute('href')
                    description = await desc_el.inner_text()
                    full_link = link if link.startswith('http') else f"https://kwork.ru{link}"
                    jobs.append({"id": full_link, "title": f"[Kwork] {title.strip()}", "link": full_link, "description": description.strip()})
                    
            await browser.close()
            logging.info(f"Playwright: Kwork успешно спарсен ({len(jobs)} заказов).")
    except Exception as e:
        logging.error(f"Ошибка Playwright при парсинге Kwork: {e}")
    return jobs

# ==========================================
# ОСНОВНОЙ РАБОЧИЙ ЦИКЛ
# ==========================================
async def scan_freelance_boards():
    try:
        await bot.send_message(TELEGRAM_USER_ID, "🚀 <b>Скайнет v11.0 запущен!</b>\nВключено управление ключами прямо из чата.")
    except Exception as e:
        logging.error(f"Ошибка TG: {e}")
        
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                logging.info("Сканирую ленты (FL + Kwork)...")
                force_scan_event.clear() 
                
                # Подтягиваем актуальные ключи из БД перед каждой проверкой
                current_keys = get_keywords()
                compiled_keywords = [re.compile(rf'\b{re.escape(k)}\b', re.IGNORECASE) for k in current_keys]
                
                def contains_keywords(title: str) -> bool:
                    return any(pattern.search(title) for pattern in compiled_keywords)
                
                fl_jobs = await fetch_rss_feed(session, "FL", RSS_FEEDS["FL"])
                kwork_jobs = await fetch_kwork_jobs()
                all_jobs = fl_jobs + kwork_jobs
                
                new_matches = 0
                for job in all_jobs:
                    if is_job_seen(job['id']):
                        continue
                    
                    mark_job_seen(job['id'])
                    
                    if contains_keywords(job['title']):
                        cover_letter = await generate_cover_letter(job['title'], job['description'])
                        if "SKIP" in cover_letter.upper():
                            continue
                        
                        new_matches += 1
                        
                        # Сохраняем в Excel
                        save_to_excel(job['title'], job['link'], cover_letter)
                        
                        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Открыть заказ на бирже", url=job['link'])]])
                        msg = f"🔥 <b>Новый заказ!</b>\n\n<b>Название:</b> {job['title']}\n\n🤖 <b>Сгенерированный отклик:</b>\n<code>{cover_letter}</code>"
                        
                        try:
                            await bot.send_message(TELEGRAM_USER_ID, msg, reply_markup=kb)
                        except Exception as e:
                            logging.error(f"Ошибка отправки: {e}")
                
                logging.info(f"Проверка завершена. Жду {CHECK_INTERVAL} сек.")
                await asyncio.wait_for(force_scan_event.wait(), timeout=CHECK_INTERVAL)
                logging.info("⚡ Запущен принудительный поиск по кнопке!")
                
            except asyncio.TimeoutError:
                pass 
            except Exception as e:
                logging.error(f"Глобальная ошибка в цикле парсинга: {e}. Перезапуск через 10 секунд...")
                await asyncio.sleep(10)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    init_db()
    
    scanner_task = asyncio.create_task(scan_freelance_boards())
    polling_task = asyncio.create_task(dp.start_polling(bot))
    
    try:
        await asyncio.gather(scanner_task, polling_task)
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("Остановлено вручную.")
            break
        except Exception as e:
            logging.error(f"Падение Event Loop: {e}. Бронебойный рестарт через 5 сек...")
            import time
            time.sleep(5)