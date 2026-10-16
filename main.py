
import os
import re
import tempfile
import threading
from pathlib import Path

import requests
import yt_dlp
from flask import Flask, request, abort

app = Flask(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
BASE_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Ограничиваем размер видео, чтобы не превышать лимит Telegram.
MAX_SIZE = 49 * 1024 * 1024


def telegram(method, data=None, files=None, timeout=120):
    response = requests.post(
        f"{API_URL}/{method}",
        data=data,
        files=files,
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))

    return result["result"]


def send_message(chat_id, text, reply_to=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_to:
        data["reply_to_message_id"] = reply_to

    telegram("sendMessage", data=data)


def is_reels_url(text):
    pattern = (
        r"https?://(?:www\.)?instagram\.com/"
        r"(?:reel|reels)/[A-Za-z0-9_-]+"
    )
    return re.search(pattern, text, re.IGNORECASE)


def download_reel(url, folder):
    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(Path(folder) / "video.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_SIZE,
        "socket_timeout": 25,
        "retries": 1,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])

    videos = list(Path(folder).glob("video.*"))

    if not videos:
        raise RuntimeError("Видео не удалось скачать")

    video = videos[0]

    if video.stat().st_size > MAX_SIZE:
        raise RuntimeError("Видео превышает допустимый размер")

    return video


def process_message(message):
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    text = message.get("text", "")

    match = is_reels_url(text)

    if not match:
        return

    url = match.group(0)

    send_message(
        chat_id,
        "⏳ Получил ссылку на Reels. Пробую загрузить видео...",
        message_id,
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            video_path = download_reel(url, folder)

            with open(video_path, "rb") as video:
                telegram(
                    "sendVideo",
                    data={
                        "chat_id": chat_id,
                        "caption": "Видео из Instagram Reels",
                        "supports_streaming": "true",
                        "reply_to_message_id": message_id,
                    },
                    files={
                        "video": (
                            "reel.mp4",
                            video,
                            "video/mp4",
                        )
                    },
                    timeout=180,
                )

    except Exception as error:
        print(f"Ошибка обработки Reels: {error}")

        send_message(
            chat_id,
            "Не удалось загрузить видео. Возможно, ролик закрытый, "
            "Instagram ограничил доступ или видео слишком большое. "
            "Попробуй другую общедоступную ссылку.",
            message_id,
        )


@app.route("/", methods=["GET"])
def home():
    return "Reels bot is running", 200


@app.route("/webhook/" + WEBHOOK_SECRET, methods=["POST"])
def webhook():
    update = request.get_json(silent=True)

    if not update:
        abort(400)

    message = update.get("message") or update.get("channel_post")

    if message and message.get("text"):
        # Обрабатываем сообщение в отдельном потоке,
        # чтобы Telegram быстро получил подтверждение.
        threading.Thread(
            target=process_message,
            args=(message,),
            daemon=True,
        ).start()

    return "OK", 200


def setup_webhook():
    webhook_url = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"

    result = telegram(
        "setWebhook",
        data={
            "url": webhook_url,
            "allowed_updates": '["message", "channel_post"]',
        },
    )

    print(f"Webhook configured: {result}")


if __name__ == "__main__":
    setup_webhook()

    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
    )
