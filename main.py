import os
import re
import logging
import asyncio
import base64
from pathlib import Path

import httpx
import yt_dlp
from fastapi import FastAPI, Request

# =========================
# CONFIG
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Limites (ajuste no Railway > Variables)
MAX_MB = int(os.getenv("MAX_MB", "45"))
MAX_MINUTES = int(os.getenv("MAX_MINUTES", "12"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# Segurança e Concorrência
# ALLOWED_USERS_IDS: separados por vírgula (ex: 1234567,9876543)
ALLOWED_USERS_IDS = [int(u) for u in (os.getenv("ALLOWED_USERS_IDS") or "").split(",") if u.strip().isdigit()]
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# Cookies (base64) — opcionais
IG_COOKIES_B64 = (os.getenv("IG_COOKIES_B64") or "").strip()  # Instagram
X_COOKIES_B64 = (os.getenv("X_COOKIES_B64") or "").strip()    # X/Twitter (raramente precisa)

IG_COOKIE_PATH = Path("cookies_ig.txt")
X_COOKIE_PATH = Path("cookies_x.txt")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not BOT_TOKEN:
    logging.error("BOT_TOKEN não definido. Configure em Railway > Variables.")


def _write_cookiefile(b64_value: str, path: Path, label: str):
    if not b64_value:
        return
    try:
        path.write_bytes(base64.b64decode(b64_value))
        logging.info("%s cookies carregados (%s criado).", label, path.name)
    except Exception as e:
        logging.error("Falha ao criar %s: %s", path.name, e)


_write_cookiefile(IG_COOKIES_B64, IG_COOKIE_PATH, "Instagram")
_write_cookiefile(X_COOKIES_B64, X_COOKIE_PATH, "X/Twitter")

app = FastAPI(title="Telegram Downloader: TikTok + Instagram + X")


# =========================
# UI HELPERS
# =========================
def platform_ui(platform: str) -> tuple[str, str]:
    ui = {
        "tiktok": ("🎵", "TikTok"),
        "instagram": ("📸", "Instagram"),
        "x": ("🐦", "X (Twitter)"),
    }
    return ui.get(platform, ("🔗", platform))


async def tg_chat_action(chat_id: int, action: str):
    """action: typing, upload_video"""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{TELEGRAM_API}/sendChatAction",
                data={"chat_id": chat_id, "action": action},
            )
            if r.status_code != 200:
                logging.warning("sendChatAction falhou: %s | %s", r.status_code, r.text)
    except Exception:
        pass


# =========================
# TELEGRAM SEND/DELETE
# =========================
async def tg_send_message(chat_id: int, text: str) -> int | None:
    """
    Envia mensagem e retorna message_id (pra conseguir apagar depois).
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            if r.status_code != 200:
                logging.error("sendMessage falhou: %s | %s", r.status_code, r.text)
                return None

            data = r.json()
            return data.get("result", {}).get("message_id")
    except Exception as e:
        logging.error("sendMessage exception: %s", e)
        return None


async def tg_delete_message(chat_id: int, message_id: int) -> bool:
    """
    Apaga mensagem do bot. Retorna True se apagou, False se falhou.
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{TELEGRAM_API}/deleteMessage",
                data={"chat_id": chat_id, "message_id": message_id},
            )
            if r.status_code != 200:
                logging.warning("deleteMessage falhou (%s): %s", r.status_code, r.text)
                return False

            data = r.json()
            if not data.get("ok"):
                logging.warning("deleteMessage ok=false: %s", data)
                return False
            return True
    except Exception as e:
        logging.warning("deleteMessage exception: %s", e)
        return False


async def tg_send_video(chat_id: int, file_path: Path):
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            with file_path.open("rb") as f:
                r = await client.post(
                    f"{TELEGRAM_API}/sendVideo",
                    data={"chat_id": chat_id},
                    files={"video": f},
                )
            if r.status_code != 200:
                logging.error("sendVideo falhou: %s | %s", r.status_code, r.text)
            r.raise_for_status()
    except Exception as e:
        logging.error("sendVideo exception: %s", e)
        raise


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


# =========================
# DETECT LINKS
# =========================
def detect_platform(link: str) -> str | None:
    link = (link or "").strip()

    if re.match(r'^(https?://)?(www\.)?(m\.)?(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/', link):
        return "tiktok"

    if re.match(r'^(https?://)?(www\.)?instagram\.com/', link):
        return "instagram"

    if re.match(r'^(https?://)?(www\.)?(x\.com|twitter\.com)/', link):
        return "x"

    return None


# =========================
# YT-DLP
# =========================
def _cookiefile_for(platform: str) -> str | None:
    if platform == "instagram" and IG_COOKIE_PATH.exists():
        return str(IG_COOKIE_PATH)
    if platform == "x" and X_COOKIE_PATH.exists():
        return str(X_COOKIE_PATH)
    return None


def extract_info_no_download(url: str, platform: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "skip_download": True,
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "extractor_args": {"tiktok": ["api_hostname=api16-normal-c-useast1a.tiktokv.com"]},
    }
    cf = _cookiefile_for(platform)
    if cf:
        ydl_opts["cookiefile"] = cf

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_media(url: str, platform: str) -> Path:
    ydl_opts = {
        "format": "bv*+ba/best",
        "merge_output_format": "mp4",
        "outtmpl": str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "quiet": True,
        "noplaylist": True,
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "extractor_args": {"tiktok": ["api_hostname=api16-normal-c-useast1a.tiktokv.com"]},
    }

    cf = _cookiefile_for(platform)
    if cf:
        ydl_opts["cookiefile"] = cf

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


# =========================
# CORE LOGIC (AUTO-DELETE PROGRESS)
# =========================
async def handle_text(chat_id: int, text: str):
    # --- Segurança: Whitelist ---
    if ALLOWED_USERS_IDS and chat_id not in ALLOWED_USERS_IDS:
        logging.info("Acesso negado para o chat_id: %s", chat_id)
        await tg_send_message(chat_id, "🚫 *Acesso negado.*\nEste bot é privado e você não está na lista de usuários permitidos.")
        return

    text = (text or "").strip()
    progress_msgs: list[int] = []

    def track(mid: int | None):
        if mid:
            progress_msgs.append(mid)

    # comandos
    if text == "/start":
        await tg_send_message(
            chat_id,
            "✅ *Online!*\n\n"
            "Me manda um link e eu devolvo o vídeo.\n"
            "*Suporta:* TikTok 🎵 | Instagram 📸 (reel/post) | X 🐦\n\n"
            f"⚙️ *Limites:* até {MAX_MINUTES} min (quando disponível) e {MAX_MB} MB.\n"
            "🧹 Depois que o vídeo chega, eu apago as mensagens de progresso."
        )
        return

    if text == "/help":
        await tg_send_message(
            chat_id,
            "📌 *Como usar*\n"
            "1) Copia o link\n"
            "2) Cola aqui\n"
            "3) Eu baixo e envio ✅\n\n"
            "*Plataformas:* TikTok 🎵 | Instagram 📸 | X 🐦"
        )
        return

    platform = detect_platform(text)
    if not platform:
        track(await tg_send_message(chat_id, "🚫 *Link inválido.* Envie um link do TikTok, Instagram ou X."))
        await asyncio.sleep(2)
        for m in progress_msgs:
            await tg_delete_message(chat_id, m)
        return

    emoji, label = platform_ui(platform)

    await tg_chat_action(chat_id, "typing")
    track(await tg_send_message(chat_id, f"{emoji} *Link detectado:* {label}\n🔎 Checando..."))

    file_path: Path | None = None

    try:
        # info
        info = await asyncio.to_thread(extract_info_no_download, text, platform)
        duration = info.get("duration")
        title = (info.get("title") or "vídeo").strip()

        if duration is not None:
            minutes = duration / 60
            if minutes > MAX_MINUTES:
                track(
                    await tg_send_message(
                        chat_id,
                        f"⏱️ *Vídeo longo* ({minutes:.1f} min).\n"
                        f"✅ *Limite:* {MAX_MINUTES} min.\n\n"
                        "Tenta um link mais curto."
                    )
                )
                await asyncio.sleep(3)
                for m in progress_msgs:
                    await tg_delete_message(chat_id, m)
                return

        # --- Controle de Concorrência (Semaphore) ---
        # Se houver muitos downloads, ele envia a mensagem e aguarda a vez dele na fila
        track(await tg_send_message(chat_id, f"⬇️ *Baixando/Na fila:* `{title[:80]}`\n⏳ Aguarde..."))
        
        async with download_semaphore:
            await tg_chat_action(chat_id, "typing")
            
            # download real
            file_path = await asyncio.to_thread(download_media, text, platform)

            if not file_path.exists():
                track(await tg_send_message(chat_id, "🚫 *Falha no download.* Arquivo não apareceu após baixar."))
                await asyncio.sleep(3)
                for m in progress_msgs:
                    await tg_delete_message(chat_id, m)
                return

            size_mb = file_size_mb(file_path)
            if size_mb > MAX_MB:
                track(
                    await tg_send_message(
                        chat_id,
                        f"📦 *Arquivo grande demais:* {size_mb:.1f} MB\n"
                        f"✅ *Limite:* {MAX_MB} MB\n\n"
                        "Tenta outro vídeo (ou um mais curto)."
                    )
                )
                await asyncio.sleep(3)
                for m in progress_msgs:
                    await tg_delete_message(chat_id, m)
                return

            await tg_chat_action(chat_id, "upload_video")
            track(await tg_send_message(chat_id, f"📤 *Enviando* ({size_mb:.1f} MB)..."))

            # envia vídeo
            await tg_send_video(chat_id, file_path)

        # 👇 importante: espera um pouquinho e apaga com retry
        await asyncio.sleep(1)

        for mid in progress_msgs:
            for _ in range(3):
                if await tg_delete_message(chat_id, mid):
                    break
                await asyncio.sleep(0.4)

        # opcional: mensagem final e apaga também
        done_id = await tg_send_message(chat_id, "✅ *Pronto!*")
        await asyncio.sleep(1)
        if done_id:
            await tg_delete_message(chat_id, done_id)

    except Exception as e:
        logging.exception("Erro no processamento")

        # limpa progresso mesmo em erro
        await asyncio.sleep(0.5)
        for mid in progress_msgs:
            for _ in range(2):
                if await tg_delete_message(chat_id, mid):
                    break
                await asyncio.sleep(0.3)

        msg = str(e)
        friendly = "❌ *Deu erro ao processar esse link.*\nTenta novamente em alguns segundos."

        if "Private" in msg or "Login" in msg or "cookies" in msg:
            friendly = (
                "🔒 *Esse conteúdo parece exigir login/permissão.*\n"
                "Se for privado/restrito, pode falhar mesmo.\n"
                "Tenta um post público."
            )
        elif "HTTP Error 429" in msg or "Too Many Requests" in msg:
            friendly = "🚦 *Muitas tentativas seguidas.*\nEspera um pouco e tenta novamente."
        elif "Unsupported URL" in msg:
            friendly = "🚫 *Link não suportado.*\nEnvia um link direto do TikTok/Instagram/X."
        elif "ffmpeg" in msg.lower():
            friendly = (
                "🧩 *Faltou o ffmpeg no servidor.*\n"
                "Confirma se o deploy foi feito com Dockerfile."
            )

        err_id = await tg_send_message(chat_id, friendly)
        await asyncio.sleep(3)
        if err_id:
            await tg_delete_message(chat_id, err_id)

    finally:
        # apaga arquivo baixado
        try:
            if file_path and file_path.exists():
                file_path.unlink()
        except Exception:
            pass


# =========================
# ROUTES
# =========================
@app.get("/")
def health():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()

    message = data.get("message") or data.get("edited_message")
    if not message:
        return {"status": "ignored"}

    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return {"status": "ignored"}

    # Dispara o handler no background sem travar o retorno do Webhook (Evita loop infinito de webhook do Telegram)
    asyncio.create_task(handle_text(int(chat_id), text))
    
    return {"status": "ok"}
