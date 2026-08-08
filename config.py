import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

VK_TOKEN = os.getenv("VK_TOKEN", "").strip()
VK_CONFIRMATION = os.getenv("VK_CONFIRMATION", "").strip()
VK_SECRET = os.getenv("VK_SECRET", "").strip()
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "").strip()
DEV_ID = os.getenv("DEV_ID", "").strip()
DEV_EMOJI = "🛠️"
VK_ME_LINK = os.getenv(
    "VK_ME_LINK",
    f"https://vk.me/club{VK_GROUP_ID}" if VK_GROUP_ID else "https://vk.me/club233542237",
)


def is_dev(user_id: int) -> bool:
    try:
        return bool(DEV_ID) and int(user_id) == int(DEV_ID)
    except (TypeError, ValueError):
        return False

VK_APP_ID = os.getenv("VK_APP_ID", "").strip()
VK_APP_SECRET = os.getenv("VK_APP_SECRET", "").strip()
# Базовый URL бэкенда (Render) — нужен для CORS и fallback-ссылки на фронтенд.
APP_BASE_URL = os.getenv("APP_BASE_URL", "").strip()
# URL фронтенда (внешний хостинг со статикой), например https://mymafia.ru
APP_FRONTEND_URL = os.getenv("APP_FRONTEND_URL", "").strip()
# Разрешённые Origin для мини-аппа (перечислить через запятую, если фронтенд на другом хосте).
APP_CORS_ORIGINS = [o.strip() for o in os.getenv("APP_CORS_ORIGINS", "").split(",") if o.strip()]
# Разрешить вход в мини-апп без подписи launch-params (для отладки в браузере).
APP_ALLOW_UNSIGNED = os.getenv("APP_ALLOW_UNSIGNED", "true").strip().lower() in {"1", "true", "yes", "on"}


def mini_app_link(room_id: str) -> str:
    if VK_APP_ID:
        return f"https://vk.com/app{VK_APP_ID}#room_id={room_id}"
    if APP_FRONTEND_URL:
        return f"{APP_FRONTEND_URL.rstrip('/')}/#room_id={room_id}"
    return f"{APP_BASE_URL}/app/#room_id={room_id}"


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_PASSWORD = (
    os.getenv("SUPABASE_PASSWORD", "").strip()
    or os.getenv("SUPBASE_PASSWORD", "").strip()
)
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL", "").strip()

WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT") or os.getenv("PORT") or "8080")
CALLBACK_PATH = os.getenv("CALLBACK_PATH", "/")

# Keep-alive: как часто пинговать свой публичный URL, чтобы Render не уснул.
# Render бесплатный тариф усыпляет сервис после ~15 минут простоя.
KEEPALIVE_INTERVAL = int(os.getenv("KEEPALIVE_INTERVAL", "600"))

MIN_PLAYERS = 4
MAX_PLAYERS = 15

REGISTRATION_SECONDS = 90
NIGHT_SECONDS = 45
DISCUSSION_SECONDS = 60
VOTE_SECONDS = 60
CONFIRM_SECONDS = 20
MORNING_DELAY = 5
LYNCH_DELAY = 2

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
NICKNAMES_FILE = os.path.join(DATA_DIR, "nicknames.json")

if not VK_TOKEN:
    raise RuntimeError("VK_TOKEN не задан. Скопируйте .env.example в .env и укажите токен группы.")
if not VK_CONFIRMATION:
    raise RuntimeError(
        "VK_CONFIRMATION не задан. Возьмите строку подтверждения из настроек сообщества: "
        "Управление > Сообщения > Callback API."
    )
