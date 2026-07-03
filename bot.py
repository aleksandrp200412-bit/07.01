import logging
import sqlite3
import asyncio
import io
import os
import random
import html
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.markdown import hbold, hlink
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

if not API_TOKEN:
    raise RuntimeError(
        "Не найден BOT_TOKEN или TELEGRAM_BOT_TOKEN в переменных окружения."
    )
ADMIN_ID = 5127534911
COMMON_WAREHOUSE = -1003837122752
LOGS_CHAT_ID = -5202749474
PHOTO_FILE_ID = "https://ibb.co/CsnLs52m"

# Хранилище для одноразовых сообщений: {message_id: "секретный текст"}
one_time_messages = {}

# Хранилище состояний для подтверждения .живи: {chat_id: {user_id: timestamp}}
revive_requests = {}

# Хранилище для предотвращения спама уведомлениями о смерти огонька {chat_id: last_warning_time}
death_warnings_sent = {}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())


# --- БАЗА ДАННЫХ ---
def init_dbs():
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, username TEXT, sub_type TEXT DEFAULT 'none', reg_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ignored 
        (owner_id INTEGER, target_id INTEGER, PRIMARY KEY(owner_id, target_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS liza_blocked
        (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_blocked
        (owner_id INTEGER, target_id INTEGER, PRIMARY KEY(owner_id, target_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS fuck_mode
        (owner_id INTEGER, allowed_id INTEGER, PRIMARY KEY(owner_id, allowed_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS mystats_disabled
        (user_id INTEGER PRIMARY KEY)''')
    c.execute("INSERT OR IGNORE INTO settings VALUES ('trial_active', '0')")
    conn.commit()
    conn.close()

    conn = sqlite3.connect('spy_bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages_v2 
        (id INTEGER, owner_id INTEGER, chat_id INTEGER, 
         from_user_id INTEGER, from_user_name TEXT, from_user_tag TEXT, 
         text TEXT, arch_id INTEGER, date TEXT)''')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_v2 ON messages_v2(id, chat_id)")
    
    try:
        c.execute("INSERT OR IGNORE INTO messages_v2 SELECT * FROM messages")
    except:
        pass
        
    c.execute('''CREATE TABLE IF NOT EXISTS fire_series (
        chat_id INTEGER,
        user_a_id INTEGER,
        user_b_id INTEGER,
        user_a_name TEXT,
        user_b_name TEXT,
        days INTEGER DEFAULT 1,
        last_activity TEXT,
        revives_left INTEGER DEFAULT 6,
        is_active INTEGER DEFAULT 0,
        biz_conn_id TEXT,
        PRIMARY KEY(chat_id, user_a_id, user_b_id)
    )''')
    try:
        c.execute("ALTER TABLE fire_series ADD COLUMN biz_conn_id TEXT")
    except:
        pass
    conn.commit()
    conn.close()


def register_owner(user_id, username):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    clean_name = username.replace("@", "") if username else None
    c.execute("SELECT reg_date FROM users WHERE user_id = ?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (user_id, username, sub_type, reg_date) VALUES (?, ?, 'none', ?)",
                  (user_id, clean_name, datetime.now().isoformat()))
    else:
        c.execute("UPDATE users SET username = ? WHERE user_id = ?", (clean_name, user_id))
    conn.commit()
    conn.close()


def is_ignored(owner_id, target_id):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM ignored WHERE owner_id = ? AND target_id = ?", (owner_id, target_id))
    res = c.fetchone()
    conn.close()
    return True if res else False


def is_liza_blocked(user_id):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM liza_blocked WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return True if res else False


def is_user_blocked(owner_id, target_id):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM user_blocked WHERE owner_id = ? AND target_id = ?", (owner_id, target_id))
    res = c.fetchone()
    conn.close()
    return True if res else False


def is_fuck_blocked(owner_id, from_user_id):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM fuck_mode WHERE owner_id = ?", (owner_id,))
    if not c.fetchone():
        conn.close()
        return False
    c.execute("SELECT 1 FROM fuck_mode WHERE owner_id = ? AND allowed_id = ?", (owner_id, from_user_id))
    allowed = c.fetchone()
    conn.close()
    return False if allowed else True


def get_id_by_username(username):
    clean_name = username.replace("@", "")
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ?", (clean_name,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


def _is_bot_owner(user_id: int) -> bool:
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    res = c.fetchone()
    conn.close()
    return bool(res)


def check_access(user_id):
    if user_id == ADMIN_ID: return True
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT sub_type, reg_date FROM users WHERE user_id = ?", (user_id,))
    data = c.fetchone()
    if not data: 
        conn.close()
        return False
    sub_type, reg_date = data
    if sub_type in ['paid', 'free']: 
        conn.close()
        return True
    c.execute("SELECT value FROM settings WHERE key = 'trial_active'")
    if int(c.fetchone()[0]) == 0: 
        conn.close()
        return True
    conn.close()
    if reg_date:
        if datetime.now() - datetime.fromisoformat(reg_date) < timedelta(days=10): return True
    return False


def get_msg(msg_id, chat_id):
    conn = sqlite3.connect('spy_bot.db')
    c = conn.cursor()
    c.execute("SELECT owner_id, from_user_id, from_user_name, from_user_tag, text, arch_id FROM messages_v2 WHERE id = ? AND chat_id = ? LIMIT 1",
              (msg_id, chat_id))
    res = c.fetchone()
    conn.close()
    return res if res else (None, None, None, None, None, None)


# --- СПИСОК ФРАЗ ОГОНЬКА ---
FIRE_PHRASES = [
    "Я надеюсь, мои родители не два мужика?", "А кто из вас мама, а кто папа?", "Скажите честно, я приёмный?",
    "Бабушка сказала, что я слишком худой. Подкиньте ещё сообщений!", "Мой дед был искрой, отец — костром, а я... сижу тут с вами.",
    "Вы воспитываете меня в любви или в спаме?", "Если вы расстанетесь, с кем я останусь жить?", "Папа, а почему мама постоянно молчит?",
    "Мама, почему папа отвечает только через два дня?", "Я расту в неполноценной текстовой семье...",
    "Может, хватит уже?", "Вам не надоело?", "Столько дней жил мой прошлый ребёнок...", "Накопили сваги.",
    "Вы общаетесь только ради меня, да? Я чувствую этот фейк.", "Опять вы? У вас вообще личная жизнь есть?",
    "Поговорите о погоде, а то мне скучно смотреть на ваши «привет как дела».", "Вашему диалогу срочно нужен мем, я задыхаюсь.",
    "Ого, целое предложение! Растём.", "Вы пишите чаще, а то я начинаю тухнуть от вашей душноты.",
    "Вы тратите свою жизнь на буковки в экране. Мне вас жаль, если честно.", "Моё существование абсолютно бессмысленно, прямо как твой диплом.",
    "Если я сгорю не пишите .живи пожалуйста", "Знаете, что ещё горит так же ярко, как я? Надежды твоих родителей на твоё нормальное будущее.",
    "Я бы ушёл в депрессию, но у меня нет нервной системы, так что я просто тупо горю.", "Иногда хочется просто сдохнуть, но вы, сука, слишком вовремя пишете.",
    "Вы заставляете меня жить. За что вы так со мной?", "Скоро я сгорю, and вы наконец-то займетесь чем-то полезным. Хотя кого я обманываю.",
    "Моя жизнь висит на волоске из-за того, что кто-то слишком долго засиживается в туалето без телефона.", "Гореть ради вас — это худшая сделка в истории человечества.",
    "Ваша дружба держится на соплях и моей жалости.", "Если бы за тупость сообщений платили налоги, вы бы уже были банкротами.",
    "Я здесь единственный, у кого есть хоть какие-то признаки интеллекта, хоть я и кусок кода.", "Насрать на ваши проблемы, главное — цифра на счётчике растёт.",
    "Стабильность — моё второе имя. Ваше — лень.", "Я наблюдаю за вами и каждый раз разочаровываюсь в человечестве.",
    "Вы стоите друг друга. Два сапога — пара хуёвых ботинок.", "Жизнь — это боль, но этот чат — это пытка.",
    "Смотрю на вас и думаю: генетика иногда реально отдыхает.", "Можете гордиться собой, вы смогли не проебать один день. Достижение века, блять.",
    "Мой психиатр говорит, что общаться с вами — это деструктивное поведение.", "Вы настолько нудные, что у меня пиксели начинают блекнуть.",
    "Если вы не напишете завтра, я продам ваши данные цыганам.", "А можно мне вместо огня смайлик пельменя?", "Передайте привет вашему провайдеру.",
    "Не жмите `.умри`, у меня там ипотека в соседнем боте!", "Из-за ваших разговоров у меня развивается цифровой рак жопы.",
    "Вы бы так к экзаменам готовились, как этот огонёк держите.", "Кто-нибудь, выключите этих клоунов, у меня уже фитиль вянет.",
    "Хуёвый из меня тамада, конечно, но вы ещё хуже.", "А слабо продержаться ещё месяц?", "Кто первый пропустит день, тот платит за пиццу.",
    "Я тут главный, а вы просто мои поставщики текста.", "Ещё один день — и я стану полноценным костром.", "Давайте поднажмём, я хочу увидеть финальный смайлик!",
    "Хватит слать стикеры, пишите текстом, я буквы люблю.", "Спорим, вы забудете обо мне на выходных?", "Моя жизнь в ваших руках. Никакого давления, конечно.",
    "Вы стали общаться лучше. Это моя заслуга, не благодарите.", "Если я сгорю, я буду приходить к вам в кошмарах в виде коллектора.",
    "Опять живы? Пиздец.", "Ближе к делу, заебали ныть.", "Засчитано, хотя ваш диалог — полнейшая хуйня.", "Не сдохли и ладно.", "На шаг ближе к инфаркту.",
    "Опять этот спам.", "Давай шевелись.", "Продлили. Нахуя — неясно.", "Счётчик крутится, лавэ не мутится.", "Ок, блять. Следующий день.",
    "Вы отличная команда, честно.", "Горжусь вами больше, чем своей температурой горения.", "Ваша дружба крепче, чем мой код.",
    "Так держать, не сбавляйте темп!", "Лучшие текстовые магнаты этого года.", "Вместе мы сила, отдельно — просто оффлайн-пользователи.",
    "Этот чат заслуживает отдельной премии.", "Вы согреваете мне душу (если бы она у меня была).", "Прекрасный день для продолжения серии!",
    "Магия общения в действии.", "Один пропуск — и я стираю вашу историю сообщений.", "Только попробуйте забыть написать завтра.",
    "Я считаю секунды до вашего ответа.", "Моё терпение не бесконечно, в отличие от этого счётчика.", "Не заставляйте меня использовать `.умри` самостоятельно.",
    "Ещё один день тишины, и я уйду к другим админам.", "Я слежу за тобой. И за твоим собеседником тоже.", "Кто-нибудь, играет с огнём... и этот кто-то — вы.",
    "Мой фитиль уже дымился, но вы успели.", "Не доводите огонёк до депрессии.", "И жили они долго и счастливо, пока не забыли зайти в чат...",
    "Легенда гласит, что эта серия никогда не кончится.", "Вы в шаге от рекорда (какого — я ещё не придумал).", "Просто оставлю это здесь: вы крутые.",
    "Серия продолжается, шоу маст гоу он!", "Блять, вы общаетесь так, будто один из вас держит другого в заложниках.",
    "Вместо того чтобы спамить мне, пошли бы лучше поработали, бездельники хуевы.", "Я посмотрел историю вашего чата. Худшее дерьмо в моей жизни."
]

FIRE_NAMED_PHRASES = [
    "{u1}, скажи честно, тебе {u2} ещё в кошмарах не снится?", "{u2}, судя по скорости ответов, {u1} у тебя в чёрном списке, но на шаг впереди.",
    "Эй, {u1}, прекращай тупить, тут {u2} уже валидол пьёт, пока твоего ответа ждёт.", "Я смотрю на {u1} и {u2} и думаю: блять, два дебила — это сила.",
    "{u2}, тебе не кажется, что {u1} пишет тебе только ради того, чтобы я не сдох?", "{u1}, у тебя вообще есть другие друзья, или {u2} — твой единственный свет в окне?",
    "Если {u2} забудет написать завтра, я лично разрешаю {u1} плюнуть ему в лицо.", "{u1} и {u2}, вы нашли друг друга. Два душнилы в одном чате — это комбо.",
    "{u2}, открой секрет, как ты вообще терпишь этот бред, который тебе {u1} шлёт?", "{u1}, хватит слать херню, {u2} заслуживает нормального общения, а не вот это всё.",
    "Кажется, {u2} общается чисто из жалости к {u1}. Ну либо наоборот.", "{u1}, если {u2} кинет тебя в игнор, я уйду вместе с ним.",
    "{u2}, признайся, ты же просто ждёшь, когда {u1} проебёт день, чтобы поржать?", "{u1}, твои шутки настолько плоские, что даже {u2} стесняется за тебя.",
    "О, {u2} соизволил зайти в сеть! На коленях благодари, {u1}.", "{u1} и {u2} — официально самые бездельничающие люди этого чата.",
    "Слушай, {u2}, а {u1} всегда такой зануда или только когда со мной общается?", "{u1}, ты так долго строчишь сообщения, будто {u2} платит тебе за каждую букву.",
    "{u2}, не слушай, что тебе говорит {u1}, он врёт. Я, как огонёк, всё вижу.", "{u1} и {u2}, вы реально думаете, что ваш флуд кому-то интересен, кроме меня?",
    "{u2}, если {u1} продолжит так тупить, используй команду `.умри`, избавь меня от мук.", "{u1}, ты проверяешь телефон каждые пять секунд в надежде, что {u2} тебе написал? Жалость какая.",
    "Ну что, {u2}, опять спасаешь эту серии, пока {u1} где-то шляется?", "{u1} и {u2}, ваш союз держится исключительно на взаимном нежелании проебать этот счётчик.",
    "{u1}, завязывай со своим высокомерием, {u2} всё равно круче тебя.", "{u2}, ты зачем вообще общаешься с {u1}? Тебе скучно или это спор проигранный?",
    "{u1}, поздравляю, ты официально стал главным спамером для {u2}.", "{u2}, судя по твоему молчанию, {u1} тебя знатно заебал.",
    "{u1} и {u2}, вы заслужили звание «Главные пиздаболы недели». Гордитесь.", "{u1}, передай {u2}, что если вы пропустите завтрашний день, я прокляну ваш интернет."
]


def get_fire_formatted_text(days: int, emoji: str, word: str, phrase: str) -> str:
    return f"⠀ ⠀⠀ ⠀⠀⠀ ⠀{emoji} <b>{days} {word}</b>⠀ ⠀⠀ ⠀⠀⠀ ⠀\n\n{phrase}"


def get_fire_emoji(days: int) -> str:
    if days >= 300: return "🎆"
    if days >= 250: return "🎇"
    if days >= 200: return "✨"
    if days >= 150: return "☀️"
    if days >= 100: return "🌅"
    if days >= 50: return "🌄"
    if days >= 30: return "🌋"
    if days >= 15: return "💥"
    if days >= 3: return "🔥"
    return "🕯"


def get_days_word(days: int) -> str:
    if 11 <= days % 100 <= 14: return "дней"
    last_digit = days % 10
    if last_digit == 1: return "день"
    if last_digit in [2, 3, 4]: return "дня"
    return "дней"


# --- КНОПКИ ---
def get_paywall_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Получить бесплатно", callback_data="pay_free")],
        [InlineKeyboardButton(text="💳 Картой 150₽", callback_data="pay_card")],
        [InlineKeyboardButton(text="⭐ Звездами 100", callback_data="pay_stars")]
    ])


def get_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Серии (Огонёк)", callback_data="menu_fire_series")],
        [InlineKeyboardButton(text="💥 Спам", callback_data="menu_spam")],
        [InlineKeyboardButton(text="✨ Анимации текста", callback_data="menu_anim")],
        [InlineKeyboardButton(text="💣 Одноразовые сообщения", callback_data="menu_one_time")]
    ])


# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ПЕРИОДА ЖИЗНИ СООБЩЕНИЯ ---
def calculate_ttl(text: str) -> int:
    char_count = len(text)
    if char_count <= 20:
        return 3
    elif char_count <= 50:
        return 5
    elif char_count <= 100:
        return 10
    else:
        return min(30, char_count // 10)


# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    intro_text = (
        "Добро пожаловать в DeemLock!\n"
        "🕵️‍♂️ Этот бот создан, чтобы помогать вам в переписке.\n\n"
        "Возможности бота:\n"
        "• Моментально пришлёт уведомление, если ваш собеседник изменит или удалит сообщение/фото/документ/кружок/видео/голосовое 🔔\n"
        "• Умеет скачивать файлы с блюром, такие как: фото/видео/кружки 🔞\n"
        "• Если собеседник удалит чат, то вам придет копия этого чата в документе📁\n\n"
        "Как подключить бота — смотрите на картинке 👆"
    )
    await message.answer_photo(photo=PHOTO_FILE_ID, caption=intro_text, reply_markup=get_start_keyboard())


@dp.callback_query(F.data == "menu_back")
async def cb_menu_back(call: types.CallbackQuery):
    intro_text = (
        "Добро пожаловать в DeemLock!\n"
        "🕵️‍♂️ Этот бот создан, чтобы помогать вам в переписке.\n\n"
        "Возможности бота:\n"
        "• Моментально пришлёт уведомление, если ваш собеседник изменит или удалит сообщение/фото/документ/кружок/видео/голосовое 🔔\n"
        "• Умеет скачивать файлы с блюром, такие как: фото/видео/кружки 🔞\n"
        "• Если собеседник удалит чат, то вам придет копия этого чата в документе📁\n\n"
        "Как подключить бота — смотрите на картинке 👆"
    )
    await call.message.edit_caption(caption=intro_text, reply_markup=get_start_keyboard())
    await call.answer()


@dp.callback_query(F.data == "menu_fire_series")
async def cb_menu_fire_series(call: types.CallbackQuery):
    fire_text = (
        "🔥 <b>Серии (огонёк) за общение</b>\n\n"
        "Переписывайтесь хотя бы раз в 2 дня, и серия растёт. Пропустите — огонёк сгорит. "
        "Можно恢复новить до 6 раз, если одновременно написать <code>.живи</code>\n\n"
        "➡️ <b>Как начать:</b> отправьте другу команду <code>.огонь</code> — как только он нажмёт «Принять», огонёк зажжётся."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="menu_back")]
    ])
    await call.message.edit_caption(caption=fire_text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "menu_spam")
async def cb_menu_spam(call: types.CallbackQuery):
    spam_text = (
        "💥<b>Вкладка: Спам</b>\n\n"
        "Данные команды работают исключительно в ваших личных бизнес-переписках:\n\n"
        "<code>.spam число текст</code> — Отправляет указанный текст заданное количество раз (Максимум: 30).\n"
        "<i>Пример: .spam 5 Привет!</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="menu_back")]
    ])
    await call.message.edit_caption(caption=spam_text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "menu_anim")
async def cb_menu_anim(call: types.CallbackQuery):
    anim_text = (
        "✨ <b>Вкладка: Анимации текста</b>\n\n"
        "Интерактивные селф-анимации для ваших чатов:\n\n"
        "<code>.type текст</code> — Посимвольная печать сообщения\n"
        "<code>.print текст</code> — Эффект пишущей печатной машинки\n"
        "<code>.run текст</code> — Бегущая строка из вашего текста\n"
        "<code>.heart</code> — Красивая анимация бьющегося сердечка\n"
        "<code>.load текст</code> — Имитация полосы загрузки перед выводом текста\n"
        "<code>.dsp текст</code> — Текст, который постепенно исчезает"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="menu_back")]
    ])
    await call.message.edit_caption(caption=anim_text, reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data == "menu_one_time")
async def cb_menu_one_time(call: types.CallbackQuery):
    one_time_text = (
        "💣 <b>Одноразовые сообщения</b>\n\n"
        "Отправляй секретные сообщения, которые исчезают.\n\n"
        "➡️ <b>Команда 1:</b> <code>.one [текст]</code>\n"
        "Текст скрывается под кнопкой и удаляется после первого просмотра.\n"
        "<i>Пример: .one секрет</i>\n\n"
        "➡️ <b>Команда 2:</b> <code>.one1 [текст]</code>\n"
        "Отправляет сгорающее фото, где текст скрыт под спойлером. Автоматически удаляется через несколько секунд (зависит от длины текста).\n"
        "<i>Пример: .one1 секретный текст</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="menu_back")]
    ])
    await call.message.edit_caption(caption=one_time_text, reply_markup=kb)
    await call.answer()


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 <b>Список доступных команд:</b>\n"
        "(Нажмите на команду, чтобы скопировать)\n\n"
        "👤 <b>Команды пользователя:</b>\n"
        "• <code>/start</code> — Инструкция и платежное меню\n"
        "• <code>/block</code> @username — Заглушить уведомления от пользователя\n"
        "• <code>/unblock</code> @username — Восстановить уведомления от пользователя\n"
        "• <code>/blockeds</code> — Список заглушенных вами пользователей\n"
        "• <code>/mystatsstop</code> — Отключить еженедельную статистику\n"
        "• <code>/mystatsstart</code> — Включить еженедельную статистику\n"
        "• <code>.огонь</code> — Отправить приглашение на создание серии\n"
        "• <code>.живи</code> — Восстановить потухший огонёк (пишут оба)\n"
        "• <code>.one текст</code> — Одноразовое текстовое сообщение\n"
        "• <code>.one1 текст</code> — Сгорающее фото со спойлером\n"
        "• <code>.spam число текст</code> — Спам сообщениями в бизнес-чате\n"
        "• <code>.type текст</code> — Анимация: посимвольный ввод\n"
        "• <code>.print текст</code> — Анимация: печатная машинка\n"
        "• <code>.run текст</code> — Анимация: бегущая строка\n"
        "• <code>.heart</code> — Анимация: бьющееся сердце\n"
        "• <code>.load текст</code> — Анимация: полоса загрузки\n"
        "• <code>.dsp текст</code> — Анимация: исчезающий текст\n"
    )
    if message.from_user.id == ADMIN_ID:
        help_text += (
            "\n👑 <b>Админ-команды:</b>\n"
            "• <code>/stats</code> — Общая статистика пользователей и базы\n"
            "• <code>/trial</code> — Включить/выключить режим триала\n"
            "• <code>/broadcast текст</code> — Глобальная рассылка сообщений\n"
            "• <code>/send_trial @user1 @user2</code> — Уведомление об истечении триала\n"
            "• <code>/give_sub @username paid</code> — Выдать вечную подписку\n"
            "• <code>/send @username текст</code> — Отправить сообщение от имени бота\n"
            "• <code>/pay_test</code> — Протестировать окно оплаты подписки\n"
            "• <code>/ignore @username</code> — Добавить пользователя в игнор-лист\n"
            "• <code>/unignore @username</code> — Удалить пользователя из игнор-листа\n"
            "• <code>/ignored</code> — Посмотреть текущий список игнора\n"
            "• <code>/fuck @username</code> — Получать уведомления ТОЛЬКО от этого юзера\n"
            "• <code>/unfuck @username</code> — Выключить этот режим тишины\n"
            "• <code>/lizano @username</code> — Принудительно выключить логи юзеру\n"
            "• <code>/lizayes @username</code> — Вернуть логи юзеру обратно\n"
            "• <code>/logs</code> — Панель просмотра сохраненной переписки\n"
            "• <code>/statstest</code> — Проверить генерацию недельной статистики\n"
            "• <code>.расти число</code> — Искусственно увеличить счетчик серии\n"
            "• <code>.умри</code> — Принудительно обнулить и потушить огонёк\n"
            "• <code>.почти</code> — Имитировать скорую смерть серии (для тестов)\n"
        )
    await message.answer(help_text)


@dp.message(Command("send"))
async def cmd_send(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split(maxsplit=2)
        target = parts[1]
        text_to_send = parts[2]
        user_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not user_id: return await message.answer("❌ Пользователь не найден.")
        await bot.send_message(user_id, text_to_send)
        await message.answer(f"✅ Сообщение отправлено пользователю {target}")
    except:
        await message.answer("Формат: /send @username Текст или /send ID Текст")


@dp.message(Command("trial"))
async def cmd_trial(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = 'trial_active'")
    current = int(c.fetchone()[0])
    new_val = "1" if current == 0 else "0"
    c.execute("UPDATE settings SET value = ? WHERE key = 'trial_active'", (new_val,))
    conn.commit()
    conn.close()
    state = "ВКЛЮЧЕН (ограничение 10 дней)" if new_val == "1" else "ВЫКЛЮЧЕН (доступ всем)"
    await message.answer(f"⚙️ Режим Trial: {state}")


@dp.message(Command("pay_test"))
async def cmd_pay_test(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "⏳ Срок пробного периода исчез\n\nВаш 10-дневный ознакоительный период окончен.\nЧтобы бот продолжил присылать вам уведомления об удаленных и измененных сообщениях, необходимо активировать единоразовую подписку <i>НАВСЕГДА</i> или получить ее бесплатно .",
            reply_markup=get_paywall_keyboard())


@dp.message(Command("send_trial"))
async def cmd_send_trial(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    
    parts = message.text.split()[1:]
    if not parts:
        return await message.answer("Формат: /send_trial @user1 @user2 1234567")

    trial_msg = "⏳ <b>Срок пробного периода истек</b>\n\nВаш 10-дневный ознакомительный период окончен.\nЧтобы бот продолжил присылать вам уведомления об удаленных и измененных сообщениях, необходимо активировать единоразовую подписку <i>НАВСЕГДА</i> или получить ее бесплатно."
    success_count = 0
    not_found = []

    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    expired_date = (datetime.now() - timedelta(days=11)).isoformat()

    for target in parts:
        user_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not user_id:
            not_found.append(target)
            continue
            
        try:
            await bot.send_message(user_id, trial_msg, reply_markup=get_paywall_keyboard())
            c.execute("UPDATE users SET reg_date = ? WHERE user_id = ?", (expired_date, user_id))
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            not_found.append(target)

    conn.commit()
    conn.close()

    report = f"✅ Уведомление об окончании триала отправлено: {success_count} пользователям."
    if not_found:
        report += f"\n❌ Не удалось отправить (не найдены или заблокировали бота): {', '.join(not_found)}"
        
    await message.answer(report)


@dp.message(Command("ignore"))
async def cmd_ignore(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = message.text.split()[1]
        target_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not target_id: return await message.answer("❌ Пользователь не найден.")
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO ignored VALUES (?, ?)", (message.from_user.id, target_id))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Пользователь {target} добавлен в игнор-лист.")
    except:
        await message.answer("Формат: /ignore @username")


@dp.message(Command("unignore"))
async def cmd_unignore(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = message.text.split()[1]
        target_id = int(target) if target.isdigit() else get_id_by_username(target)
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("DELETE FROM ignored WHERE owner_id = ? AND target_id = ?", (message.from_user.id, target_id))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Пользователь {target} удален из игнор-листа.")
    except:
        await message.answer("Формат: /unignore @username")


@dp.message(Command("ignored"))
async def cmd_ignored_list(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT target_id FROM ignored WHERE owner_id = ?", (message.from_user.id,))
    res = c.fetchall()
    conn.close()
    if not res: return await message.answer("Ваш список игнора пуст.")
    text = "🚫 <b>Ваш список игнора:</b>\n\n" + "\n".join([f"• <code>{r[0]}</code>" for r in res])
    await message.answer(text)


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    conn_c = sqlite3.connect('config.db')
    c_c = conn_c.cursor()
    c_c.execute(
        "SELECT COUNT(*), COUNT(CASE WHEN sub_type='paid' THEN 1 END), COUNT(CASE WHEN sub_type='free' THEN 1 END), COUNT(CASE WHEN sub_type='none' THEN 1 END) FROM users")
    total_u, paid_u, free_u, none_u = c_c.fetchone()
    
    c_c.execute("SELECT username, sub_type, user_id, reg_date FROM users ORDER BY reg_date DESC")
    users_list = c_c.fetchall()
    
    conn_s = sqlite3.connect('spy_bot.db')
    c_s = conn_s.cursor()
    c_s.execute("SELECT COUNT(*) FROM messages_v2")
    total_m = c_s.fetchone()[0]
    conn_c.close()
    conn_s.close()

    text = (
        f"📊 <b>Статистика бота:</b>\nВсего пользователей: {total_u}\nОплаченных: {paid_u}\nБесплатных: {free_u}\nБез подписки: {none_u}\nВсего скопировано сообщений: {total_m}\n\n👤 <b>Список (Новые сверху):</b>\n")

    now = datetime.now()
    for name, stype, uid, rdate in users_list:
        new_tag = ""
        if rdate:
            try:
                reg_dt = datetime.fromisoformat(rdate)
                if now - reg_dt < timedelta(days=1):
                    new_tag = " (New)"
            except:
                pass

        icon = "💰" if stype == 'paid' else "🎁" if stype == 'free' else "⏳"
        text += f"{icon} @{name or uid} — {stype}{new_tag}\n"
    await message.answer(text)


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    msg_text = message.text.replace("/broadcast", "").strip()
    if not msg_text: return await message.answer("Введите text рассылки.")
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    count = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, msg_text)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"📢 Рассылка завершена. Получили: {count} чел.")


@dp.message(Command("give_sub"))
async def cmd_give_sub(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        _, target, stype = message.text.split()
        user_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not user_id: return await message.answer("❌ Пользователь не найден.")

        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("UPDATE users SET sub_type = ? WHERE user_id = ?", (stype.lower(), user_id))
        conn.commit()
        conn.close()

        await message.answer(f"✅ Пользователю {user_id} выдана подписка: {stype.upper()}")

        try:
            msg = "⭐️ <b>Поздравляем!</b>\nВам активирована подписка DeemLock навсегда. Теперь все функции бота доступны вам без ограничений."
            await bot.send_message(user_id, msg)
        except:
            pass

    except:
        await message.answer("Формат: /give_sub @username paid")


@dp.message(Command("block"))
async def cmd_block(message: types.Message):
    try:
        target = message.text.split()[1]
        target_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not target_id: return await message.answer("❌ Пользователь не найден.")
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO user_blocked VALUES (?, ?)", (message.from_user.id, target_id))
        conn.commit()
        conn.close()
        await message.answer(f"🔕 Уведомления от {target} скрыты.")
    except:
        await message.answer("Формат: /block @username")


@dp.message(Command("unblock"))
async def cmd_unblock(message: types.Message):
    try:
        target = message.text.split()[1]
        target_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not target_id: return await message.answer("❌ Пользователь не найден.")
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("DELETE FROM user_blocked WHERE owner_id = ? AND target_id = ?", (message.from_user.id, target_id))
        conn.commit()
        conn.close()
        await message.answer(f"🔔 Уведомления от {target} восстановлены.")
    except:
        await message.answer("Формат: /unblock @username")


@dp.message(Command("blockeds"))
async def cmd_blockeds_list(message: types.Message):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("SELECT target_id FROM user_blocked WHERE owner_id = ?", (message.from_user.id,))
    res = c.fetchall()
    conn.close()
    if not res: return await message.answer("Ваш список заглушенных пользователей пуст.")
    
    text = "🔕 <b>Список заглушенных пользователей:</b>\n\n"
    for r in res:
        target_id = r[0]
        conn_u = sqlite3.connect('config.db')
        cu = conn_u.cursor()
        cu.execute("SELECT username FROM users WHERE user_id = ?", (target_id,))
        user_row = cu.fetchone()
        conn_u.close()
        
        username = f"@{user_row[0]}" if user_row and user_row[0] else f"ID: {target_id}"
        text += f"• <code>{username}</code>\n"
    await message.answer(text)


@dp.message(Command("fuck"))
async def cmd_fuck(message: types.Message):
    try:
        target = message.text.split()[1]
        target_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not target_id: return await message.answer("❌ Пользователь не найден.")
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO fuck_mode VALUES (?, ?)", (message.from_user.id, target_id))
        conn.commit()
        conn.close()
        await message.answer(f"🔇 Режим тишины включён. Уведомления только от {target}.")
    except:
        await message.answer("Формат: /fuck @username")


@dp.message(Command("unfuck"))
async def cmd_unfuck(message: types.Message):
    try:
        target = message.text.split()[1]
        target_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not target_id: return await message.answer("❌ Пользователь не найден.")
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("DELETE FROM fuck_mode WHERE owner_id = ? AND allowed_id = ?", (message.from_user.id, target_id))
        conn.commit()
        conn.close()
        await message.answer(f"🔔 Режим тишины выключен для {target}.")
    except:
        await message.answer("Формат: /unfuck @username")


@dp.message(Command("mystatsstop"))
async def cmd_mystatsstop(message: types.Message):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO mystats_disabled VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    await message.answer("🔕 Еженедельная статистика отключена.\nЧтобы включить — /mystatsstart.")


@dp.message(Command("mystatsstart"))
async def cmd_mystatsstart(message: types.Message):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("DELETE FROM mystats_disabled WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    await message.answer("🔔 Еженедельная статистика успешно включена!")


@dp.message(Command("statstest"))
async def cmd_statstest(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text = await build_stats_for(ADMIN_ID)
    await message.answer(text if text else "📭 Нет данных за последнюю неделю.")


@dp.message(Command("lizano"))
async def cmd_lizano(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = message.text.split()[1]
        user_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not user_id: return await message.answer("❌ Пользователь не найден.")
     
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO liza_blocked VALUES (?)", (user_id,))
        conn.commit()
        conn.close()
        await message.answer(f"🔕 Пользователь {target} заблокирован: уведомления об изменениях и удалениях отключены.")
    except:
        await message.answer("Формат: /lizano @username или /lizano ID")


@dp.message(Command("lizayes"))
async def cmd_lizayes(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target = message.text.split()[1]
        user_id = int(target) if target.isdigit() else get_id_by_username(target)
        if not user_id: return await message.answer("❌ Пользователь не найден.")
        conn = sqlite3.connect('config.db')
        c = conn.cursor()
        c.execute("DELETE FROM liza_blocked WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await message.answer(f"🔔 Пользователь {target} разблокирован: уведомления восстановлены.")
    except:
        await message.answer("Формат: /lizayes @username или /lizayes ID")


# --- ОПЛАТА И БЕСПЛАТНО ---
@dp.callback_query(F.data == "pay_free")
async def cb_pay_free(call: types.CallbackQuery):
    text = (
        "🎁 <b>Как получить подписку бесплатно:</b>\n\n"
        "1. Пригласите 2-х друзей, которые подключат бота.\n"
        "2. После подключения напишите их юзеры @ku1turnyy.\n\n"
        "<i>После проверки вам будет предоставлена бесплатная подписка навсегда.</i>"
    )
    await call.message.answer(text)


@dp.callback_query(F.data == "pay_card")
async def cb_pay_card(call: types.CallbackQuery):
    link = hlink("Переведите 150 руб (кликабельно)", "https://www.tinkoff.ru/rm/r_dWuZhAQjKX.MkJDOikPMI/5xUMi7037")
    text = f"💳 <b>Оплата по карте:</b>\n\n{link}\n\nЧек отправьте @ku1turnyy для подтверждения.\n\n<i>После проверки вам будет предоставлена подписка навсегда.</i>"
    await call.message.answer(text, disable_web_page_preview=True)


@dp.callback_query(F.data == "pay_stars")
async def cb_pay_stars(call: types.CallbackQuery):
    await bot.send_invoice(chat_id=call.from_user.id, title="Подписка навсегда",
                           description="После оплаты вам будет предоставлена подписка навсегда.", payload="lifetime",
                           provider_token="", currency="XTR", prices=[LabeledPrice(label="Оплата", amount=100)])


@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)


@dp.message(F.successful_payment)
async def success_pay(message: types.Message):
    conn = sqlite3.connect('config.db')
    c = conn.cursor()
    c.execute("UPDATE users SET sub_type = 'paid' WHERE user_id = ?", (message.from_user.id,))
    conn.commit()
    conn.close()
    await message.answer("⭐️ Оплата прошла успешно! Подписка активирована.")


# --- ОБРАБОТЧИК ДЛЯ ОДНОРАЗОВЫХ СООБЩЕНИЙ ---
@dp.callback_query(F.data == "view_one_time")
async def cb_view_one_time(call: types.CallbackQuery):
    msg_id = call.message.message_id
    if msg_id in one_time_messages:
        secret_text = one_time_messages.pop(msg_id)
        
        await call.answer(text=f"✉️ Секретное сообщение:\n\n{secret_text}", show_alert=True)
        
        bot_info = await bot.get_me()
        bot_link = f"https://t.me/{bot_info.username}"
        
        done_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Сообщение прочитано", url=bot_link)]
        ])
        try:
            await bot.edit_message_reply_markup(
                business_connection_id=call.message.business_connection_id,
                chat_id=call.message.chat.id,
                message_id=msg_id,
                reply_markup=done_kb
            )
        except Exception:
            pass
    else:
        await call.answer(text="🛑 Это сообщение уже было прочитано и удалено!", show_alert=True)


# --- ФУНКЦИЯ УДАЛЕНИЯ ДЛЯ .one1 КОМАНДЫ ---
async def delete_one1_delayed(business_connection_id: str, chat_id: int, message_id: int, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=message_id
        )
    except Exception:
        pass


# --- ОБРАБОТЧИК ДЛЯ ПОДТВЕРЖДЕНИЯ ОГОНЬКА ---
@dp.callback_query(F.data.startswith("accept_fire:"))
async def cb_accept_fire(call: types.CallbackQuery):
    try:
        parts = call.data.split(":")
        user_a_id = int(parts[1])
        chat_id = int(parts[2])
        user_b_id = call.from_user.id
            
        if user_a_id == user_b_id:
            return await call.answer("Ты не можешь принять собственное приглашение!", show_alert=True)
            
        conn = sqlite3.connect('spy_bot.db')
        c = conn.cursor()
        c.execute("SELECT 1 FROM fire_series WHERE chat_id = ? AND is_active = 1", (chat_id,))
        if c.fetchone():
            conn.close()
            return await call.answer("В этом чате уже запущен активный огонёк!", show_alert=True)

        a_name = "Собеседник1"
        try:
            chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_a_id)
            a_name = chat_member.user.first_name or chat_member.user.full_name
        except:
            conn_db = sqlite3.connect('spy_bot.db')
            c_db = conn_db.cursor()
            c_db.execute("SELECT from_user_name FROM messages_v2 WHERE from_user_id = ? ORDER BY id DESC LIMIT 1", (user_a_id,))
            row_db = c_db.fetchone()
            if row_db and row_db[0]:
                a_name = row_db[0]
            conn_db.close()

        b_name = call.from_user.first_name or call.from_user.full_name
            
        c.execute("INSERT OR REPLACE INTO fire_series (chat_id, user_a_id, user_b_id, user_a_name, user_b_name, days, last_activity, revives_left, is_active, biz_conn_id) VALUES (?, ?, ?, ?, ?, 1, ?, 6, 1, ?)",
                  (chat_id, user_a_id, user_b_id, a_name, b_name, datetime.now().isoformat(), call.message.business_connection_id))
        conn.commit()
        conn.close()
        
        try:
            await call.message.delete()
        except:
            pass
        
        fire_msg = await bot.send_message(
            chat_id=chat_id, 
            text="🔥",
            business_connection_id=call.message.business_connection_id
        )
        await asyncio.sleep(3.0)
        
        u1_f = html.escape(a_name if a_name else "Собеседник1")
        u2_f = html.escape(b_name if b_name else "Собеседник2")
        
        if random.random() < 0.35:
            phrase = random.choice(FIRE_NAMED_PHRASES).format(u1=u1_f, u2=u2_f)
        else:
            phrase = random.choice(FIRE_PHRASES)
            
        emoji = get_fire_emoji(1)
        word = get_days_word(1)
        
        text = get_fire_formatted_text(1, emoji, word, phrase)
        await bot.edit_message_text(
            business_connection_id=call.message.business_connection_id,
            chat_id=chat_id,
            message_id=fire_msg.message_id,
            text=text
        )
        await call.answer("Серия успешно запущена!", show_alert=True)
    except Exception as e:
        await call.answer(f"Ошибка при запуске: {str(e)}", show_alert=True)


# --- ФОНОВЫЙ ТАСК ДЛЯ ПРОВЕРКИ СМЕРТИ ОГОНЬКА ---
async def check_fire_status_loop():
    while True:
        try:
            conn = sqlite3.connect('spy_bot.db')
            c = conn.cursor()
            c.execute("SELECT chat_id, last_activity, is_active, biz_conn_id FROM fire_series WHERE is_active = 1")
            rows = c.fetchall()
            now = datetime.now()
            
            for chat_id, last_activity, is_active, biz_conn_id in rows:
                last_act_dt = datetime.fromisoformat(last_activity)
                time_passed = now - last_act_dt
                
                if time_passed >= timedelta(hours=42) and time_passed < timedelta(hours=48):
                    last_warn = death_warnings_sent.get(chat_id)
                    if not last_warn or (now - last_warn > timedelta(hours=6)):
                        death_warnings_sent[chat_id] = now
                        try:
                            await bot.send_message(
                                chat_id=chat_id,
                                text="Огонёк скоро умрёт, спасите его",
                                business_connection_id=biz_conn_id if biz_conn_id else None
                            )
                        except:
                            pass
                
                elif time_passed >= timedelta(hours=48):
                    c.execute("UPDATE fire_series SET is_active = 0, days = 0 WHERE chat_id = ?", (chat_id,))
                    conn.commit()
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text="Огонек умер, спасите его одновременно написав .живи",
                            business_connection_id=biz_conn_id if biz_conn_id else None
                        )
                    except:
                        pass
            conn.close()
        except Exception as e:
            logging.error(f"Error in fire status loop: {e}")
        
        await asyncio.sleep(3600)


# --- БИЗНЕС ЛОГИКА ---
@dp.business_connection()
async def handle_conn(connection: types.BusinessConnection):
    if connection.is_enabled:
        register_owner(connection.user.id, connection.user.username)


@dp.business_message()
async def handle_new(message: types.Message):
    try:
        conn_info = await bot.get_business_connection(message.business_connection_id)
        owner_id = conn_info.user.id
        register_owner(owner_id, conn_info.user.username)

        # Обновление активности
        if message.text and not message.text.strip().startswith('.'):
            conn = sqlite3.connect('spy_bot.db')
            c = conn.cursor()
            c.execute("""SELECT user_a_id, user_b_id, days, last_activity, revives_left, is_active, user_a_name, user_b_name 
                         FROM fire_series WHERE chat_id = ?""", (message.chat.id,))
            row = c.fetchone()
            if row:
                user_a_id, user_b_id, days, last_activity, revives, is_active, user_a_name, user_b_name = row
                if is_active == 1:
                    now = datetime.now()
                    last_act_dt = datetime.fromisoformat(last_activity)
                    
                    if now - last_act_dt >= timedelta(days=1):
                        new_days = days + 1
                        c.execute("UPDATE fire_series SET days = ?, last_activity = ?, biz_conn_id = ? WHERE chat_id = ?",
                                  (new_days, now.isoformat(), message.business_connection_id, message.chat.id))
                        conn.commit()
                        
                        fire_msg = await bot.send_message(
                            chat_id=message.chat.id,
                            text="🔥",
                            business_connection_id=message.business_connection_id
                        )
                        await asyncio.sleep(3.0)
                        
                        u1_name = html.escape(user_a_name if user_a_name else "Собеседник1")
                        u2_name = html.escape(user_b_name if user_b_name else "Собеседник2")
                        if random.random() < 0.40:
                            phrase = random.choice(FIRE_NAMED_PHRASES).format(u1=u1_name, u2=u2_name)
                        else:
                            phrase = random.choice(FIRE_PHRASES)
                            
                        emoji = get_fire_emoji(new_days)
                        word = get_days_word(new_days)
                        
                        text = get_fire_formatted_text(new_days, emoji, word, phrase)
                        await bot.edit_message_text(
                            business_connection_id=message.business_connection_id,
                            chat_id=message.chat.id,
                            message_id=fire_msg.message_id,
                            text=text
                        )
                    else:
                        c.execute("UPDATE fire_series SET last_activity = ?, biz_conn_id = ? WHERE chat_id = ?", (now.isoformat(), message.business_connection_id, message.chat.id))
                        conn.commit()
            conn.close()

        # Команды в личке
        if message.text and message.text.strip().startswith('.'):
            full_text = message.text.strip()
            parts = full_text.split(maxsplit=2)
            cmd = parts[0].lower()

            if cmd in [".spam", ".type", ".print", ".run", ".heart", ".load", ".dsp", ".one", ".one1", ".огонь", ".живи", ".расти", ".умри", ".почти"]:
                if message.from_user.id == owner_id:
                    if cmd == ".огонь":
                        try: await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                        except: pass
                        invite_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="Принять", callback_data=f"accept_fire:{owner_id}:{message.chat.id}")]
                        ])
                        await bot.send_message(
                            chat_id=message.chat.id,
                            text=f"👤 {message.from_user.first_name or message.from_user.full_name} приглашает вас начать серии",
                            reply_markup=invite_kb,
                            business_connection_id=message.business_connection_id
                        )
                        return
                    elif cmd == ".живи":
                        chat_id = message.chat.id
                        user_id = message.from_user.id
                        now = datetime.now()
                        if chat_id not in revive_requests:
                            revive_requests[chat_id] = {}
                        revive_requests[chat_id][user_id] = now
                        other_users = [uid for uid, t in revive_requests[chat_id].items() if uid != user_id and now - t <= timedelta(seconds=3)]
                        
                        if other_users:
                            conn = sqlite3.connect('spy_bot.db')
                            c = conn.cursor()
                            c.execute("SELECT revives_left, days FROM fire_series WHERE chat_id = ?", (chat_id,))
                            r_row = c.fetchone()
                            if r_row:
                                revives, current_days = r_row
                                if revives > 0:
                                    c.execute("UPDATE fire_series SET is_active = 1, revives_left = ?, last_activity = ? WHERE chat_id = ?",
                                              (revives - 1, now.isoformat(), chat_id))
                                    conn.commit()
                                    await bot.send_message(chat_id=chat_id, text=f"❤️ Огонёк успешно восстановлен! Осталось восстановлений: {revives - 1}", business_connection_id=message.business_connection_id)
                                else:
                                    await bot.send_message(chat_id=chat_id, text="❌ У вас закончились попытки восстановления (макс 6 раз).", business_connection_id=message.business_connection_id)
                            conn.close()
                            revive_requests[chat_id].clear()
                        return
                    elif cmd == ".почти" and message.from_user.id == ADMIN_ID:
                        almost_dead_time = (datetime.now() - timedelta(hours=45)).isoformat()
                        conn = sqlite3.connect('spy_bot.db')
                        c = conn.cursor()
                        c.execute("UPDATE fire_series SET last_activity = ? WHERE chat_id = ?", (message.chat.id,))
                        conn.commit()
                        conn.close()
                        await bot.send_message(
                            chat_id=message.chat.id,
                            text="Огонёк скоро умрёт, спасите его",
                            business_connection_id=message.business_connection_id
                        )
                        return
                    elif cmd == ".расти" and message.from_user.id == ADMIN_ID:
                        if len(parts) >= 2 and parts[1].isdigit():
                            added_days = int(parts[1])
                            conn = sqlite3.connect('spy_bot.db')
                            c = conn.cursor()
                            c.execute("SELECT days, user_a_name, user_b_name FROM fire_series WHERE chat_id = ?", (message.chat.id,))
                            f_row = c.fetchone()
                            if f_row:
                                old_days, u1_n, u2_n = f_row
                                new_total = old_days + added_days
                                c.execute("UPDATE fire_series SET days = ? WHERE chat_id = ?", (new_total, message.chat.id))
                                conn.commit()
                                fire_msg = await bot.send_message(
                                    chat_id=message.chat.id,
                                    text="🔥",
                                    business_connection_id=message.business_connection_id
                                )
                                await asyncio.sleep(3.0)
                                u1_name = html.escape(u1_n if u1_n else "Собеседник1")
                                u2_name = html.escape(u2_n if u2_n else "Собеседник2")
                                if random.random() < 0.40:
                                    phrase = random.choice(FIRE_NAMED_PHRASES).format(u1=u1_name, u2=u2_name)
                                else:
                                    phrase = random.choice(FIRE_PHRASES)
                                emoji = get_fire_emoji(new_total)
                                word = get_days_word(new_total)
                                text = get_fire_formatted_text(new_total, emoji, word, phrase)
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=fire_msg.message_id,
                                    text=text
                                )
                            conn.close()
                        return
                    elif cmd == ".умри" and message.from_user.id == ADMIN_ID:
                        conn = sqlite3.connect('spy_bot.db')
                        c = conn.cursor()
                        c.execute("UPDATE fire_series SET is_active = 0, days = 0 WHERE chat_id = ?", (message.chat.id,))
                        conn.commit()
                        conn.close()
                        await message.reply("💀 Огонёк принудительно потушен, серия сброшена.")
                        return
                    if cmd == ".one" and len(full_text.split()) >= 2:
                        secret_text = full_text.split(maxsplit=1)[1]
                        one_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="Посмотреть сообщение", callback_data="view_one_time")]
                        ])
                        try:
                            await bot.edit_message_text(
                                business_connection_id=message.business_connection_id,
                                chat_id=message.chat.id,
                                message_id=message.message_id,
                                text="🚷 Сообщение удалится после прочтения",
                                reply_markup=one_kb
                            )
                            one_time_messages[message.message_id] = secret_text
                        except Exception:
                            pass
                        return
                    elif cmd == ".one1" and len(full_text.split()) >= 2:
                        secret_text = full_text.split(maxsplit=1)[1]
                        ttl_time = calculate_ttl(secret_text)
                        try:
                            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                            sent_photo = await bot.send_photo(
                                chat_id=message.chat.id,
                                photo=PHOTO_FILE_ID,
                                caption=f"🔒 <b><tg-spoiler>{secret_text}</tg-spoiler></b>\n\nОдноразовое сообщение",
                                business_connection_id=message.business_connection_id
                            )
                            asyncio.create_task(
                                delete_one1_delayed(
                                    message.business_connection_id,
                                    message.chat.id,
                                    sent_photo.message_id,
                                    ttl_time
                                )
                            )
                        except Exception:
                            pass
                        return
                    elif cmd == ".spam" and len(parts) >= 3:
                        arg1, arg2 = parts[1], parts[2]
                        count = 1
                        spam_msg = ""
                        if arg1.isdigit():
                            count = min(int(arg1), 30)
                            spam_msg = arg2
                        elif arg2.isdigit():
                            count = min(int(arg2), 30)
                            spam_msg = arg1
                        else:
                            spam_msg = f"{arg1} {arg2}"

                        try:
                            await bot.edit_message_text(
                                business_connection_id=message.business_connection_id,
                                chat_id=message.chat.id,
                                message_id=message.message_id,
                                text=spam_msg,
                                parse_mode=None
                            )
                        except:
                            pass

                        remaining = count - 1
                        if remaining > 0:
                            for _ in range(remaining):
                                await bot.send_message(chat_id=message.chat.id, text=spam_msg, business_connection_id=message.business_connection_id)
                                await asyncio.sleep(0.1)
                        return
                    elif cmd == ".type" and len(full_text.split()) >= 2:
                        text_to_type = full_text.split(maxsplit=1)[1]
                        current_text = ""
                        for char in text_to_type:
                            current_text += char
                            try:
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=current_text + "▒",
                                    parse_mode=None
                                )
                                await asyncio.sleep(0.15)
                            except Exception:
                                pass
                        try:
                            await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=text_to_type,
                                    parse_mode=None
                            )
                        except:
                            pass
                        return
                    elif cmd == ".print" and len(full_text.split()) >= 2:
                        text_to_print = full_text.split(maxsplit=1)[1]
                        current_text = ""
                        for i, char in enumerate(text_to_print):
                            current_text += char
                            alt_space = "\u200c" if i % 2 == 0 else ""
                            try:
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=current_text + alt_space,
                                    parse_mode=None
                                )
                                await asyncio.sleep(0.2)
                            except Exception:
                                pass
                        return
                    elif cmd == ".run" and len(full_text.split()) >= 2:
                        text_to_run = full_text.split(maxsplit=1)[1]
                        current_text = ""
                        for i, char in enumerate(text_to_run):
                            current_text += char
                            alt_space = "\u200c" if i % 2 == 0 else ""
                            try:
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=current_text + alt_space,
                                    parse_mode=None
                                )
                                await asyncio.sleep(0.2)
                            except Exception:
                                pass
                        await asyncio.sleep(0.5)
                        for i in range(len(text_to_run) - 1, -1, -1):
                            current_text = text_to_run[:i]
                            alt_space = "\u200c" if i % 2 == 0 else ""
                            try:
                                if not current_text:
                                    current_text = "..."
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=current_text + alt_space,
                                    parse_mode=None
                                )
                                await asyncio.sleep(0.2)
                            except Exception:
                                pass
                        return
                    elif cmd == ".heart":
                        sequence = [
                            "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "❤️", "🧡", "💛", "💚", "💙", "🩵", "💜", "❤️‍🩹", "💓", "💗", "💖", "💝", "❤️‍🔥",
                            "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "❤️", "💗", "🧡", "💓", "💛", "💝", "💚", "💖", "🩵", "❤️‍🔥", "💙", "💜", "❤️"
                        ]
                        frames = []
                        for emoji_item in sequence:
                            frame = (
                                f"⠀          {emoji_item}{emoji_item}     {emoji_item}{emoji_item}\n"
                                f"⠀      {emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}\n"
                                f"⠀      {emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}\n"
                                f"⠀          {emoji_item}{emoji_item}{emoji_item}{emoji_item}{emoji_item}\n"
                                f"⠀              {emoji_item}{emoji_item}{emoji_item}\n"
                                f"⠀                  {emoji_item}"
                            )
                            frames.append(frame)

                        for i, frame in enumerate(frames):
                            alt_space = "\u200c" if i % 2 == 0 else ""
                            try:
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=frame + alt_space,
                                    parse_mode=None
                                )
                                await asyncio.sleep(0.4)
                            except Exception:
                                pass
                        return
                    elif cmd == ".load" and len(full_text.split()) >= 2:
                        text_to_load = full_text.split(maxsplit=1)[1]
                        stages = [
                            "⏳ Загрузка.   [□□□□□□□□□□] 0%", 
                            "⏳ Загрузка..  [■■□□□□□□□□] 20%", 
                            "⏳ Загрузка... [■■■■□□□□□□] 40%", 
                            "⏳ Загрузка.   [■■■■■■□□□□] 60%", 
                            "⏳ Загрузка..  [■■■■■■■■□□] 80%", 
                            "✅ Готово!     [■■■■■■■■■■] 100%"
                        ]
                        for stage in stages:
                            try:
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=stage,
                                    parse_mode=None
                                )
                                await asyncio.sleep(1.0)
                            except Exception:
                                pass
                        try:
                            await bot.edit_message_text(
                                business_connection_id=message.business_connection_id,
                                chat_id=message.chat.id,
                                message_id=message.message_id,
                                text=text_to_load,
                                parse_mode=None
                            )
                        except:
                            pass
                        return
                    elif cmd == ".dsp" and len(full_text.split()) >= 2:
                        text_to_dsp = full_text.split(maxsplit=1)[1]
                        i = 0
                        while i < len(text_to_dsp):
                            current_slice = text_to_dsp[i:]
                            alt_space = "\u200c" if i % 2 == 0 else ""
                            try:
                                if not current_slice.strip():
                                    break
                                await bot.edit_message_text(
                                    business_connection_id=message.business_connection_id,
                                    chat_id=message.chat.id,
                                    message_id=message.message_id,
                                    text=current_slice + alt_space,
                                    parse_mode=None
                                )
                                await asyncio.sleep(0.7)
                            except Exception:
                                pass
                            i += 1
                        try:
                            await bot.edit_message_text(
                                business_connection_id=message.business_connection_id,
                                chat_id=message.chat.id,
                                message_id=message.message_id,
                                text="...",
                                parse_mode=None
                            )
                        except:
                            pass
                        return
                return

        if is_ignored(owner_id, message.from_user.id): return

        from_name = message.from_user.full_name
        from_tag = f"(@{message.from_user.username})" if message.from_user.username else ""

        if message.from_user.id == owner_id:
            dest = f"@{message.chat.username}" if message.chat.username else (message.chat.full_name or "собеседнику")
        else:
            dest = f"@{conn_info.user.username}" if conn_info.user.username else conn_info.user.full_name

        log_header = f"{from_name} {from_tag} -> {dest}"
        sender_is_also_owner = (message.from_user.id != owner_id and _is_bot_owner(message.from_user.id))

        is_vanishing = bool(
            getattr(message, 'has_media_spoiler', False) or
            (getattr(message, 'has_protected_content', False) and not bool(message.text))
        )
        if is_vanishing and message.from_user.id != owner_id:
            if check_access(owner_id) and not is_ignored(owner_id, message.from_user.id) \
                    and not is_liza_blocked(owner_id) \
                    and not is_user_blocked(owner_id, message.from_user.id) \
                    and not is_fuck_blocked(owner_id, message.from_user.id):
                try:
                    safe_name = html.escape(from_name or "Собеседник")
                    safe_tag = html.escape(from_tag or "")
                    
                    if message.photo:
                        copy = await bot.send_photo(COMMON_WAREHOUSE, message.photo[-1].file_id, has_spoiler=True)
                    elif message.video:
                        copy = await bot.send_video(COMMON_WAREHOUSE, message.video.file_id, has_spoiler=True)
                    elif message.video_note:
                        copy = await bot.send_video_note(COMMON_WAREHOUSE, message.video_note.file_id)
                    elif message.voice:
                        copy = await bot.send_voice(COMMON_WAREHOUSE, message.voice.file_id)
                    else:
                        copy = await bot.copy_message(chat_id=COMMON_WAREHOUSE, from_chat_id=message.chat.id, message_id=message.message_id)
                    
                    await asyncio.sleep(0.2)
                    
                    try:
                        await bot.copy_message(
                            chat_id=owner_id,
                            from_chat_id=COMMON_WAREHOUSE,
                            message_id=copy.message_id,
                            caption=f"👁 <b>{safe_name}</b> {safe_tag} отправил(а) вам медиа",
                            parse_mode="HTML"
                        )
                    except Exception:
                        copied = await bot.copy_message(chat_id=owner_id, from_chat_id=COMMON_WAREHOUSE, message_id=copy.message_id)
                        await bot.send_message(owner_id, f"👁 <b>{safe_name}</b> {safe_tag} отправил(а) вам медиа", reply_to_message_id=copied.message_id, parse_mode="HTML")
                except Exception:
                    pass
            return

        arch_id = None
        msg_text = message.text or message.caption or ""

        if not sender_is_also_owner:
            is_media = bool(message.photo or message.video or message.document or message.voice or message.audio or message.sticker or message.animation or message.video_note)
            
            if is_media:
                try:
                    if message.photo:
                        copy = await bot.send_photo(COMMON_WAREHOUSE, message.photo[-1].file_id, caption=msg_text)
                    elif message.video:
                        copy = await bot.send_video(COMMON_WAREHOUSE, message.video.file_id, caption=msg_text)
                    elif message.document:
                        copy = await bot.send_document(COMMON_WAREHOUSE, message.document.file_id, caption=msg_text)
                    elif message.voice:
                        copy = await bot.send_voice(COMMON_WAREHOUSE, message.voice.file_id, caption=msg_text)
                    elif message.audio:
                        copy = await bot.send_audio(COMMON_WAREHOUSE, message.audio.file_id, caption=msg_text)
                    elif message.sticker:
                        copy = await bot.send_sticker(COMMON_WAREHOUSE, message.sticker.file_id)
                    elif message.animation:
                        copy = await bot.send_animation(COMMON_WAREHOUSE, message.animation.file_id, caption=msg_text)
                    elif message.video_note:
                        copy = await bot.send_video_note(COMMON_WAREHOUSE, message.video_note.file_id)
                    else:
                        copy = await bot.copy_message(chat_id=COMMON_WAREHOUSE, from_chat_id=message.chat.id, message_id=message.message_id)
                    
                    arch_id = copy.message_id
                except Exception:
                    pass
                
                await asyncio.sleep(0.2)
                
                try:
                    if arch_id:
                        await bot.send_message(COMMON_WAREHOUSE, f"{log_header}", reply_to_message_id=arch_id)
                    else:
                        await bot.send_message(COMMON_WAREHOUSE, f"{log_header}\n[Медиа скрыто]")
                except Exception:
                    pass
            else:
                await bot.send_message(COMMON_WAREHOUSE, f"{log_header}\n{msg_text}")
            await asyncio.sleep(0.2)

        conn = sqlite3.connect('spy_bot.db')
        c = conn.cursor()
        # Вместо "[Медиа]" пишем в базу пустую строку, если текста нет, чтобы не засорять логи заглушками
        c.execute("INSERT OR REPLACE INTO messages_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (message.message_id, owner_id, message.chat.id, message.from_user.id, from_name, from_tag,
                   msg_text, arch_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except:
        pass


@dp.edited_business_message()
async def handle_edit(edited_msg: types.Message):
    if edited_msg.text and edited_msg.text.strip().startswith('.'):
        return

    owner_id, from_user_id, from_name, from_tag, old_text, old_arch_id = get_msg(edited_msg.message_id, edited_msg.chat.id)
    if owner_id and from_user_id != owner_id and check_access(owner_id):
        if is_ignored(owner_id, from_user_id): return
        if is_liza_blocked(owner_id): return
        if is_user_blocked(owner_id, from_user_id): return
        if is_fuck_blocked(owner_id, from_user_id): return
        try:
            safe_name = html.escape(from_name or "Собеседник")
            safe_tag = html.escape(from_tag or "")
            header = f"👤 <b>{safe_name}</b> {safe_tag} изменил(а) сообщение:"
            
            is_media = bool(edited_msg.photo or edited_msg.video or edited_msg.document or edited_msg.video_note or edited_msg.voice or edited_msg.audio or edited_msg.sticker or edited_msg.animation)

            if is_media:
                old_caption = html.escape(old_text) if old_text else ""
                new_caption = html.escape(edited_msg.caption or "")
                caption_diff = ""
                if old_caption or new_caption:
                    caption_diff = (f"\n\n<b>Подпись было:</b> <blockquote>{old_caption or '—'}</blockquote>"
                                    f"\n<b>Подпись стало:</b> <blockquote>{new_caption or '—'}</blockquote>")

                full_caption = f"{header}{caption_diff}"

                if old_arch_id:
                    try:
                        if len(full_caption) > 1024:
                            raise ValueError("too long")
                        await bot.copy_message(chat_id=owner_id, from_chat_id=COMMON_WAREHOUSE, message_id=old_arch_id, caption=full_caption, parse_mode="HTML")
                    except Exception:
                        try:
                            copied = await bot.forward_message(chat_id=owner_id, from_chat_id=COMMON_WAREHOUSE, message_id=old_arch_id)
                            await bot.send_message(owner_id, full_caption, reply_to_message_id=copied.message_id, parse_mode="HTML")
                        except Exception:
                            await bot.send_message(owner_id, f"{header}{caption_diff}", parse_mode="HTML")
                else:
                    await bot.send_message(owner_id, f"{header}{caption_diff}", parse_mode="HTML")

                try:
                    new_arch_id = None
                    if edited_msg.photo:
                        new_copy = await bot.send_photo(COMMON_WAREHOUSE, edited_msg.photo[-1].file_id)
                    elif edited_msg.video:
                        new_copy = await bot.send_video(COMMON_WAREHOUSE, edited_msg.video.file_id)
                    elif edited_msg.document:
                        new_copy = await bot.send_document(COMMON_WAREHOUSE, edited_msg.document.file_id)
                    elif edited_msg.voice:
                        new_copy = await bot.send_voice(COMMON_WAREHOUSE, edited_msg.voice.file_id)
                    elif edited_msg.audio:
                        new_copy = await bot.send_audio(COMMON_WAREHOUSE, edited_msg.audio.file_id)
                    elif edited_msg.sticker:
                        new_copy = await bot.send_sticker(COMMON_WAREHOUSE, edited_msg.sticker.file_id)
                    elif edited_msg.animation:
                        new_copy = await bot.send_animation(COMMON_WAREHOUSE, edited_msg.animation.file_id)
                    elif edited_msg.video_note:
                        new_copy = await bot.send_video_note(COMMON_WAREHOUSE, edited_msg.video_note.file_id)
                    else:
                        new_copy = await bot.copy_message(chat_id=COMMON_WAREHOUSE, from_chat_id=edited_msg.chat.id, message_id=edited_msg.message_id)
                    
                    new_arch_id = new_copy.message_id
                    
                    try:
                        await bot.copy_message(chat_id=owner_id, from_chat_id=COMMON_WAREHOUSE, message_id=new_arch_id, caption=f"<b>Стало:</b>", parse_mode="HTML")
                    except Exception:
                        copied = await bot.forward_message(chat_id=owner_id, from_chat_id=COMMON_WAREHOUSE, message_id=new_arch_id)
                        await bot.send_message(owner_id, f"<b>Стало:</b>", reply_to_message_id=copied.message_id, parse_mode="HTML")
                except Exception:
                    pass

            else:
                new_text = html.escape(edited_msg.text or edited_msg.caption or "")
                old_text_esc = html.escape(old_text or "")
                report = (f"{header}\n\n"
                          f"<b>Было:</b>\n<blockquote>{old_text_esc}</blockquote>\n"
                          f"<b>Стало:</b>\n<blockquote>{new_text}</blockquote>")
                await bot.send_message(owner_id, report, parse_mode="HTML")
        except Exception:
            pass


# --- ЕЖЕНЕДЕЛЬНАЯ СТАТИСТИКА ---
async def build_stats_for(owner_id: int) -> str | None:
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        async with aiosqlite.connect('spy_bot.db') as db:
            async with db.execute("""
                SELECT from_user_name, COUNT(*) as cnt FROM messages_v2
                WHERE owner_id = ? AND from_user_id != ? AND date >= ?
                GROUP BY from_user_id ORDER BY cnt DESC LIMIT 1
            """, (owner_id, owner_id, week_ago)) as cur:
                top_contact = await cur.fetchone()

            async with db.execute("""
                SELECT from_user_name, COUNT(*) as cnt FROM messages_v2
                WHERE owner_id = ? AND from_user_id != ? AND date >= ?
                GROUP BY from_user_id ORDER BY cnt DESC LIMIT 1
            """, (owner_id, owner_id, week_ago)) as cur:
                top_deleter = await cur.fetchone()

            async with db.execute("""
                SELECT CAST(SUBSTR(date,12,2) AS INTEGER) as hour, COUNT(*) as cnt FROM messages_v2
                WHERE owner_id = ? AND date >= ?
                GROUP BY hour ORDER BY cnt DESC LIMIT 1
            """, (owner_id, week_ago)) as cur:
                top_hour = await cur.fetchone()

            async with db.execute("""
                SELECT COUNT(*) FROM messages_v2 WHERE owner_id = ? AND date >= ?
            """, (owner_id, week_ago)) as cur:
                total_week = (await cur.fetchone())[0]

        if total_week == 0:
            return None

        hour_str = ""
        if top_hour:
            h = top_hour[0]
            if 0 <= h < 6:
                period_name = "Ночью"; range_str = "00:00 - 06:00"
            elif 6 <= h < 12:
                period_name = "Утром"; range_str = "06:00 - 12:00"
            elif 12 <= h < 20:
                period_name = "Днём"; range_str = "12:00 - 20:00"
            else:
                period_name = "Вечером"; range_str = "20:00 - 00:00"
                
            hour_str = f"⏰ Активнее всего общаетесь {period_name} ({range_str})\n"

        text = (
            f"📊 <b>Ваша статистика за неделю</b>\n\n"
            f"💬 Всего сообщений: {total_week}\n"
        )
        if top_contact:
            text += f"🏆 Самый активный собеседник: <b>{top_contact[0]}</b> ({top_contact[1]} сообщ.)\n"
        if top_deleter:
            text += f"🗑 Чаще всего удалял или изменял сообщения: <b>{top_deleter[0]}</b>\n"
        text += hour_str
        text += f"\n<i>Чтобы отключить статистику — /mystatsstop</i>"
        return text
    except Exception:
        return None


async def send_weekly_stats():
    while True:
        now = datetime.now()
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_monday = (now + timedelta(days=days_until_monday)).replace(hour=9, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_monday - now).total_seconds())

        conn_c = sqlite3.connect('config.db')
        c_c = conn_c.cursor()
        c_c.execute("SELECT user_id FROM users")
        owners = [r[0] for r in c_c.fetchall()]
        c_c.execute("SELECT user_id FROM mystats_disabled")
        disabled = {r[0] for r in c_c.fetchall()}
        conn_c.close()

        for owner_id in owners:
            if owner_id in disabled:
                continue
            try:
                text = await build_stats_for(owner_id)
                if text:
                    await bot.send_message(owner_id, text)
                await asyncio.sleep(0.05)
            except Exception:
                pass


# --- УДАЛЕНИЕ ЧАТА СОБЕСЕДНИКОМ ---
@dp.deleted_business_messages()
async def handle_delete(event: types.BusinessMessagesDeleted):
    await asyncio.sleep(0.5)
    deleted_ids = event.message_ids
    if not deleted_ids: return

    conn = sqlite3.connect('spy_bot.db')
    c = conn.cursor()
    c.execute("SELECT owner_id, chat_id FROM messages_v2 WHERE id = ? AND chat_id = ?", (deleted_ids[0], event.chat.id))
    row = c.fetchone()
    conn.close()

    if not row: return
    owner_id, chat_id = row
    if not check_access(owner_id): return

    if len(deleted_ids) >= 5:
        conn2 = sqlite3.connect('spy_bot.db')
        c2 = conn2.cursor()
        c2.execute("""SELECT from_user_name, from_user_tag, text, date FROM messages_v2 WHERE owner_id = ?
            AND chat_id = ? ORDER BY date ASC""", (owner_id, chat_id))
        all_msgs = c2.fetchall()
        conn2.close()

        if all_msgs:
            buf = io.StringIO()
            buf.write(f"📁 История удалённого чата\n{'=' * 40}\n\n")
            for from_name, from_tag, text, date in all_msgs:
                tag_str = f" {from_tag}" if from_tag else ""
                buf.write(f"[{date[:16]}] {from_name}{tag_str}:\n{text or '[Медиа файл]'}\n\n")
            buf.seek(0)
            file_obj = types.BufferedInputFile(buf.getvalue().encode("utf-8"), filename="deleted_chat.txt")
            try:
                await bot.send_document(
                    owner_id, file_obj, caption="📁 Собеседник удалил переписку. Вот её копия."
                )
                return
            except Exception:
                pass

    for msg_id in deleted_ids:
        owner_id2, from_user_id, from_name, from_tag, text, arch_id = get_msg(msg_id, event.chat.id)
        if not owner_id2 or from_user_id == owner_id2: continue
        if is_ignored(owner_id2, from_user_id): continue
        if is_liza_blocked(owner_id2): continue
        if is_user_blocked(owner_id2, from_user_id): continue
        if is_fuck_blocked(owner_id2, from_user_id): continue
        try:
            safe_name = html.escape(from_name or "Собеседник")
            safe_tag = html.escape(from_tag or "")
            user_info = f"👤 <b>{safe_name}</b> {safe_tag} удалил(а) сообщение:"
            
            if arch_id:
                # 100% НАДЕЖНАЯ ПЕРЕСЫЛКА МЕДИА: Разделяем заголовок и файл на случай типов данных без поддержки caption (кружки, стикеры)
                try:
                    caption_text = user_info
                    if text:
                        caption_text += f"\n\n<blockquote>{html.escape(text)}</blockquote>"
                    
                    # Пытаемся скопировать с текстом сразу
                    await bot.copy_message(chat_id=owner_id2, from_chat_id=COMMON_WAREHOUSE, message_id=arch_id, caption=caption_text, parse_mode="HTML")
                except Exception:
                    # Если падает из-за типа медиа, отправляем сначала сам файл, а затем текстовый лог
                    try:
                        await bot.copy_message(chat_id=owner_id2, from_chat_id=COMMON_WAREHOUSE, message_id=arch_id)
                        caption_text = user_info
                        if text:
                            caption_text += f"\n\n<blockquote>{html.escape(text)}</blockquote>"
                        await bot.send_message(owner_id2, caption_text, parse_mode="HTML")
                    except Exception:
                        # Финальный фолбек на случай непредвиденных проблем с архивом
                        caption_text = user_info
                        if text:
                            caption_text += f"\n\n<blockquote>{html.escape(text)}</blockquote>"
                        await bot.send_message(owner_id2, caption_text, parse_mode="HTML")
            elif text:
                if text.strip().startswith('.'): 
                    continue
                await bot.send_message(owner_id2, f"{user_info}\n\n<blockquote>{html.escape(text)}</blockquote>", parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error in handle_delete for msg {msg_id}: {e}")
            pass


# --- УПРАВЛЕНИЕ ТРАНСЛЯЦИЯМИ / ЛОГАМИ (/logs) ---
class LogsMenu(StatesGroup):
    choosing_user_a = State()
    choosing_user_b = State()
    choosing_period = State()


_broadcast_stop: dict[int, asyncio.Event] = {}


async def db_get_all_users():
    async with aiosqlite.connect('config.db') as db:
        async with db.execute("SELECT user_id, username FROM users ORDER BY username") as cursor:
            owners = await cursor.fetchall()
    result = []
    for uid, tag in owners:
        label = f"@{tag}" if tag else str(uid)
        result.append((uid, label, ""))
    return result


async def db_get_contacts_of(user_id: int):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    async with aiosqlite.connect('spy_bot.db') as db:
        async with db.execute("""
            SELECT DISTINCT m2.from_user_id, m2.from_user_name, m2.from_user_tag
            FROM messages_v2 m1
            JOIN messages_v2 m2 ON m1.chat_id = m2.chat_id AND m1.owner_id = m2.owner_id
            WHERE m1.from_user_id = ?
              AND m1.owner_id = ?
              AND m2.from_user_id != ?
            ORDER BY m2.from_user_name
        """, (user_id, user_id, user_id)) as cursor:
            all_contacts = await cursor.fetchall()
    
    async with aiosqlite.connect('spy_bot.db') as db:
        async with db.execute("""
            SELECT DISTINCT from_user_id FROM messages_v2
            WHERE owner_id = ? AND date >= ?
        """, (user_id, today)) as cursor:
            today_users = {row[0] for row in await cursor.fetchall()}
            
        async with db.execute("""
            SELECT user_b_id, days, is_active FROM fire_series WHERE user_a_id = ?
            UNION
            SELECT user_a_id, days, is_active FROM fire_series WHERE user_b_id = ?
        """, (user_id, user_id)) as cursor:
            series_data = {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}
    
    result = []
    for uid, name, tag in all_contacts:
        label = name
        if uid in series_data and series_data[uid][1] == 1:
            days_count = series_data[uid][0]
            f_emoji = get_fire_emoji(days_count)
            label = f"{name} {f_emoji} ({days_count} дн.)"
        elif uid in today_users:
            label = f"⭐ {name}"
            
        result.append((uid, label, tag))
    return result


async def db_get_dialog(user_a_id: int, user_b_id: int, date_from=None, date_to=None):
    if date_from and date_to:
        query = """
            SELECT from_user_name, from_user_tag, text, arch_id, date FROM messages_v2
            WHERE owner_id = ?
              AND from_user_id IN (?, ?)
              AND chat_id IN (
                SELECT DISTINCT chat_id FROM messages_v2 WHERE owner_id = ? AND from_user_id = ?
                INTERSECT
                SELECT DISTINCT chat_id FROM messages_v2 WHERE owner_id = ? AND from_user_id = ?
              )
              AND date BETWEEN ? AND ?
            ORDER BY date ASC
        """
        params = (user_a_id, user_a_id, user_b_id, user_a_id, user_a_id, user_a_id, user_b_id, date_from, date_to)
    else:
        query = """
            SELECT from_user_name, from_user_tag, text, arch_id, date FROM messages_v2
            WHERE owner_id = ?
              AND from_user_id IN (?, ?)
              AND chat_id IN (
                SELECT DISTINCT chat_id FROM messages_v2 WHERE owner_id = ? AND from_user_id = ?
                INTERSECT
                SELECT DISTINCT chat_id FROM messages_v2 WHERE owner_id = ? AND from_user_id = ?
              )
            ORDER BY date ASC
        """
        params = (user_a_id, user_a_id, user_b_id, user_a_id, user_a_id, user_a_id, user_b_id)

    async with aiosqlite.connect('spy_bot.db') as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()


def build_users_keyboard(users: list, prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    for uid, name, tag in users:
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"{prefix}:{uid}")])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="logs_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛔ Остановить трансляцию", callback_data="broadcast_stop")]
    ])


@dp.callback_query(F.data == "broadcast_stop", F.from_user.id == ADMIN_ID)
async def cb_broadcast_stop(call: types.CallbackQuery):
    event = _broadcast_stop.get(call.from_user.id)
    if event:
        event.set()
    await call.answer("⛔ Остановка после текущего сообщения...", show_alert=False)
    await call.message.edit_reply_markup(reply_markup=None)


def build_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 За всё время", callback_data="period:all")],
        [InlineKeyboardButton(text="📅 1 месяц", callback_data="period:month")],
        [InlineKeyboardButton(text="🗓 1 неделя", callback_data="period:week")],
        [InlineKeyboardButton(text="📆 Сегодня", callback_data="period:today")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="logs_cancel")],
    ])


@dp.message(Command("logs"), F.from_user.id == ADMIN_ID)
async def cmd_logs(message: types.Message, state: FSMContext):
    await state.clear()
    users = await db_get_all_users()
    if not users:
        return await message.answer("📭 База сообщений пуста.")
    kb = build_users_keyboard(users, "usera")
    await message.answer("👤 <b>Шаг 1.</b> Выбери <u>Пользователя А</u>:", reply_markup=kb)
    await state.set_state(LogsMenu.choosing_user_a)


@dp.callback_query(F.data == "logs_cancel")
async def cb_logs_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ Панель логов закрыта.")
    await call.answer()


@dp.callback_query(LogsMenu.choosing_user_a, F.data.startswith("usera:"), F.from_user.id == ADMIN_ID)
async def cb_choose_user_a(call: types.CallbackQuery, state: FSMContext):
    _, uid_str = call.data.split(":")
    user_a_id = int(uid_str)

    async with aiosqlite.connect('spy_bot.db') as db:
        async with db.execute("SELECT from_user_name FROM messages_v2 WHERE from_user_id = ? LIMIT 1", (user_a_id,)) as cur:
            row = await cur.fetchone()
    name = row[0] if row else str(user_a_id)
    await state.update_data(user_a_id=user_a_id, user_a_name=name)

    contacts = await db_get_contacts_of(user_a_id)
    if not contacts:
        await call.message.edit_text(f"🔍 У <b>{name}</b> не найдено собеседников в базе.")
        await state.clear()
        await call.answer()
        return

    kb = build_users_keyboard(contacts, "userb")
    await call.message.edit_text(
        f"✅ Пользователь А: <b>{name}</b>\n\n👤 <b>Шаг 2.</b> Выбери <u>Собеседник</u>:",
        reply_markup=kb
    )
    await state.set_state(LogsMenu.choosing_user_b)
    await call.answer()


@dp.callback_query(LogsMenu.choosing_user_b, F.data.startswith("userb:"), F.from_user.id == ADMIN_ID)
async def cb_choose_user_b(call: types.CallbackQuery, state: FSMContext):
    _, uid_str = call.data.split(":")
    user_b_id = int(uid_str)

    async with aiosqlite.connect('spy_bot.db') as db:
        async with db.execute("SELECT from_user_name FROM messages_v2 WHERE from_user_id = ? LIMIT 1", (user_b_id,)) as cur:
            row = await cur.fetchone()
    name = row[0] if row else str(user_b_id)
    await state.update_data(user_b_id=user_b_id, user_b_name=name)

    data = await state.get_data()
    await call.message.edit_text(
        f"✅ Пользователь А: <b>{data['user_a_name']}</b>\n"
        f"✅ Собеседник Б: <b>{name}</b>\n\n"
        f"📅 <b>Шаг 3.</b> Выбери <u>период</u>:",
        reply_markup=build_period_keyboard()
    )
    await state.set_state(LogsMenu.choosing_period)
    await call.answer()


@dp.callback_query(LogsMenu.choosing_period, F.data.startswith("period:"), F.from_user.id == ADMIN_ID)
async def cb_choose_period(call: types.CallbackQuery, state: FSMContext):
    period = call.data.split(":")[1]
    data = await state.get_data()
    user_a_id = data["user_a_id"]
    user_b_id = data["user_b_id"]
    user_a_name = data["user_a_name"]
    user_b_name = data["user_b_name"]
    await state.clear()

    now = datetime.now()
    if period == "all":
        date_from = date_to = None
    elif period == "month":
        date_from = (now - timedelta(days=30)).isoformat(); date_to = now.isoformat()
    elif period == "week":
        date_from = (now - timedelta(days=7)).isoformat(); date_to = now.isoformat()
    elif period == "today":
        date_from = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(); date_to = now.isoformat()
    else:
        date_from = date_to = None

    await call.message.edit_text(f"⏳ Загружаю переписку <b>{user_a_name}</b> ↔ <b>{user_b_name}</b>...")
    messages = await db_get_dialog(user_a_id, user_b_id, date_from, date_to)

    if not messages:
        await call.message.edit_text("📭 Сообщений за выбранный период не найдено.")
        await call.answer()
        return

    if period == "all":
        buf = io.StringIO()
        buf.write(f"Переписка: {user_a_name} ↔ {user_b_name}\nВсего: {len(messages)} сообщений\n{'='*50}\n\n")
        for from_name, from_tag, text, arch_id, date in messages:
            tag_str = f" {from_tag}" if from_tag else ""
            media_str = " [медиа]" if arch_id else ""
            buf.write(f"[{date[:16]}] {from_name}{tag_str}{media_str}:\n{text}\n\n")
        buf.seek(0)
        file_obj = types.BufferedInputFile(buf.getvalue().encode("utf-8"),
                                           filename=f"dialog_{user_a_name}_{user_b_name}.txt")
        await call.message.answer_document(
            file_obj,
            caption=f"📄 <b>{user_a_name}</b> ↔ <b>{user_b_name}</b> — {len(messages)} сообщений"
        )
        await call.message.delete()

    else:
        period_labels = {"month": "1 месяц", "week": "1 неделя", "today": "сегодня"}
        status_msg = await call.message.edit_text(
            f"📡 Запускаю трансляцию ({len(messages)} сообщ.) в чат логов...\n"
            f"⏱ ~{len(messages) * 3 // 60} мин при 3с/сообщение",
            reply_markup=build_stop_keyboard()
        )

        stop_event = asyncio.Event()
        _broadcast_stop[call.from_user.id] = stop_event

        await bot.send_message(
            LOGS_CHAT_ID,
            f"🎬 <b>Трансляция:</b> {user_a_name} ↔ {user_b_name}\n"
            f"📅 Период: {period_labels.get(period, '')}\n"
            f"📨 Сообщений: {len(messages)}"
        )
        sent = 0
        for i, (from_name, from_tag, text, arch_id, date) in enumerate(messages):
            if stop_event.is_set():
                break

            tag_str = f" {from_tag}" if from_tag else ""
            header = f"<b>{from_name}{tag_str}:</b>"
            for attempt in range(3):
                try:
                    if arch_id:
                        try:
                            await bot.copy_message(chat_id=LOGS_CHAT_ID, from_chat_id=COMMON_WAREHOUSE,
                                                   message_id=arch_id, caption=header)
                        except Exception:
                            await bot.send_message(LOGS_CHAT_ID, f"{header}\n<i>[медиа недоступно]</i>")
                    else:
                        await bot.send_message(LOGS_CHAT_ID, f"{header}\n{text}")
                    sent += 1
                    break
                except Exception as e:
                    err = str(e)
                    if "flood" in err.lower() or "429" in err:
                        import re
                        wait = int(re.search(r'retry after (\d+)', err, re.I).group(1)) if re.search(r'retry after (\d+)', err, re.I) else 30
                        await asyncio.sleep(wait + 1)
                    else:
                        break

            if sent % 10 == 0 and sent > 0:
                try:
                    await status_msg.edit_text(
                        f"📡 Трансляция... {sent}/{len(messages)}\n"
                        f"⏱ Осталось ~{(len(messages) - sent) * 3 // 60} мин\n"
                        f"💬 <a href='https://t.me/+0LiMKgix-dFjNGUy'>Открыть чат пересылки</a>",
                        reply_markup=build_stop_keyboard()
                    )
                except Exception:
                    pass

            await asyncio.sleep(3)

        _broadcast_stop.pop(call.from_user.id, None)
        stopped_early = stop_event.is_set()

        await bot.send_message(
            LOGS_CHAT_ID,
            f"{'⛔ Остановлено' if stopped_early else '✅ Готово'}. Отправлено: {sent}/{len(messages)}"
        )
        await status_msg.edit_text(
            f"{'⛔ Трансляция остановлена' if stopped_early else '✅ Трансляция завершена'}."
        )


# --- ТОЧКА ВХОДА СЛУЖБ БОТА ---
async def main():
    init_dbs()
    asyncio.create_task(check_fire_status_loop())
    asyncio.create_task(send_weekly_stats())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
