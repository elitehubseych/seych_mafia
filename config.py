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
VK_ME_LINK = os.getenv(
    "VK_ME_LINK",
    f"https://vk.me/club{VK_GROUP_ID}" if VK_GROUP_ID else "https://vk.me/club233542237",
)

WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT") or os.getenv("PORT") or "8080")
CALLBACK_PATH = os.getenv("CALLBACK_PATH", "/")

# Keep-alive: как часто пинговать свой публичный URL, чтобы Render не уснул.
# Render бесплатный тариф усыпляет сервис после ~15 минут простоя.
KEEPALIVE_INTERVAL = int(os.getenv("KEEPALIVE_INTERVAL", "600"))

MIN_PLAYERS = 4
MAX_PLAYERS = 15

REGISTRATION_SECONDS = 300
NIGHT_SECONDS = 60
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
