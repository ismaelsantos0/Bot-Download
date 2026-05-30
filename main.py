import os
import re
import time
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

MAX_MB = int(os.getenv("MAX_MB", "45"))
MAX_MINUTES = int(os.getenv("MAX_MINUTES", "12"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

ALLOWED_USERS_IDS = [int(u) for u in (os.getenv("ALLOWED_USERS_IDS") or "").split(",") if u.strip().isdigit()]
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

IG_COOKIES_B64 = (os.getenv("IG_COOKIES_B64") or "").strip()
X_COOKIES_B64 = (os.getenv("X_COOKIES_B64") or "").strip()

IG_COOKIE_PATH = Path("cookies_ig.txt")
X_COOKIE_PATH = Path("cookies_x.txt")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if not BOT_TOKEN:
    logging.error("BOT_TOKEN não definido. Configure em Railway > Variables.")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _write_cookiefile(b64_value: str, path: Path, label: str):
    if not b64_value:
        logging.warning("%s: X_COOKIES_B64 / IG_COOKIES_B64 não configurado.", label)
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
    logging.info("sendVideo: iniciando upload de %s (%.1f MB)", file_path.name, file_path.stat().st_size / 1024 / 1024)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            with file_path.open("rb") as f:
                r = await client.post(
                    f"{TELEGRAM_API}/sendVideo",
                    data={"chat_id": chat_id, "supports_streaming": "true"},
                    files={"video": (file_path.name, f, "video/mp4")},
                )
            logging.info("sendVideo resposta: %s | %s", r.status_code, r.text[:300])
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
# YT-DLP & FFMPEG
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
        "http_headers": {"User-Agent": UA},
        "extractor_args": {
            "tiktok": ["api_hostname=api16-normal-c-useast1a.tiktokv.com"],
        },
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
        "quiet": False,
        "noplaylist": True,
        "http_headers": {"User-Agent": UA},
        "extractor_args": {
            "tiktok": ["api_hostname=api16-normal-c-useast1a.tiktokv.com"],
        },
        "postprocessors": [],  
    }
    cf = _cookiefile_for(platform)
    if cf:
        ydl_opts["cookiefile"] = cf

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        real_path = None
        requested = info.get("requested_downloads") or []
        if requested:
            fp = requested[0].get("filepath")
            if fp:
                real_path = Path(fp)

        if not real_path or not real_path.exists():
            guessed = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
            if guessed.exists():
                real_path = guessed

        if not real_path or not real_path.exists():
            video_id = info.get("id", "")
            candidates = list(DOWNLOAD_DIR.glob(f"{video_id}.*"))
            if not candidates:
                candidates = sorted(DOWNLOAD_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                real_path = candidates[0]

        for _ in range(20):
            if real_path and real_path.exists() and real_path.stat().st_size > 0:
                time.sleep(1) 
                break
            time.sleep(0.5)

        return real_path


async def split_video(file_path: Path, max_mb: int) -> list[Path]:
    """Divide um vídeo grande em várias partes menores usando ffmpeg."""
    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb <= max_mb:
        return [file_path]

    cmd_probe = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)
    ]
    proc_probe = await asyncio.create_subprocess_exec(*cmd_probe, stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc_probe.communicate()
    
    try:
        duration = float(stdout.decode().strip())
    except (ValueError, TypeError):
        duration = 600.0  

    segment_time = max(10, int(duration * (max_mb / size_mb) * 0.90))

    output_pattern = file_path.with_name(f"{file_path.stem}_part%03d.mp4")
    cmd_split = [
        "ffmpeg", "-i", str(file_path),
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-reset_timestamps", "1",
        str(output_pattern)
    ]
    
    logging.info("Dividindo vídeo de %.1f MB em segmentos de %s segundos...", size_mb, segment_time)
    proc_split = await asyncio.create_subprocess_exec(
        *cmd_split, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc_split.communicate()

    parts = sorted(file_path.parent.glob(f"{file_path.stem}_part*.mp4"))
    return parts


# =========================
# CORE LOGIC
# =========================
async def handle_text(chat_id: int, text: str):
    if ALLOWED_USERS_IDS and chat_id not in ALLOWED_USERS_IDS:
        logging.info("Acesso negado para o chat_id: %s", chat_id)
        await tg_send_message(chat_id, "🚫 *Acesso negado.*\nEste bot é privado e você não está na lista de usuários permitidos.")
        return

    text = (text or "").strip()
    progress_msgs: list[int] = []

    def track(mid: int | None):
        if mid:
            progress_msgs.append(mid)

    if text == "/start":
        await tg_send_message(
            chat_id,
            "✅ *Online!*\n\n"
            "Me manda um link e eu devolvo o vídeo.\n"
            "*Suporta:* TikTok 🎵 | Instagram 📸 (reel/post) | X 🐦\n\n"
            f"⚙️ *Limites:* até {MAX_MINUTES} min.\n"
            "📦 *Grandes arquivos:* Se passar do limite do Telegram, eu corto e envio em partes automaticamente.\n\n"
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

        # ==========================================
        # NOVO: Mensagem permanente com o nome do vídeo 
        # (Não usa o `track()`, logo não será apagada)
        # ==========================================
        await tg_send_message(chat_id, f"🎬 *Título:* `{title[:120]}`")
        
        # Mensagem temporária avisando do download
        track(await tg_send_message(chat_id, "⬇️ *Baixando o vídeo...*\n⏳ Aguarde..."))

        async with download_semaphore:
            await tg_chat_action(chat_id, "typing")

            file_path = await asyncio.to_thread(download_media, text, platform)

            if not file_path or not file_path.exists():
                track(await tg_send_message(chat_id, "🚫 *Falha no download.* Arquivo não apareceu após baixar."))
                await asyncio.sleep(3)
                for m in progress_msgs:
                    await tg_delete_message(chat_id, m)
                return

            size_mb = file_size_mb(file_path)
            
            if size_mb > MAX_MB:
                track(await tg_send_message(
                    chat_id, 
                    f"📦 *Vídeo grande ({size_mb:.1f} MB).* Dividindo em partes de {MAX_MB} MB para o Telegram aceitar... ✂️"
                ))
                await tg_chat_action(chat_id, "upload_video")
                
                parts = await split_video(file_path, MAX_MB)
                
                if not parts:
                    track(await tg_send_message(chat_id, "❌ Falha ao tentar dividir o vídeo."))
                    return
                
                for i, part in enumerate(parts, start=1):
                    # Mensagem PERMANENTE avisando a parte
                    await tg_send_message(chat_id, f"📦 *Parte {i}/{len(parts)}*")
                    
                    # Mensagem TEMPORÁRIA de upload
                    upload_msg = await tg_send_message(chat_id, "📤 *Fazendo upload da parte...*")
                    track(upload_msg)
                    
                    await tg_send_video(chat_id, part)
                    await asyncio.sleep(1) 
            else:
                await tg_chat_action(chat_id, "upload_video")
                track(await tg_send_message(chat_id, f"📤 *Enviando arquivo* ({size_mb:.1f} MB)..."))
                await tg_send_video(chat_id, file_path)

        await asyncio.sleep(1)
        for mid in progress_msgs:
            for _ in range(3):
                if await tg_delete_message(chat_id, mid):
                    break
                await asyncio.sleep(0.4)

        done_id = await tg_send_message(chat_id, "✅ *Pronto!*")
        await asyncio.sleep(1)
        if done_id:
            await tg_delete_message(chat_id, done_id)

    except Exception as e:
        logging.exception("Erro no processamento")

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
        elif "Bad guest token" in msg or "guest token" in msg.lower():
            friendly = (
                "⚠️ *Erro de autenticação com o X.*\n"
                "Os cookies podem estar expirados ou ausentes.\n"
                "Renove os cookies e atualize a variável X_COOKIES_B64 no Railway."
            )
        elif "No video could be found" in msg:
            friendly = (
                "🎞️ *Nenhum vídeo encontrado nesse tweet.*\n"
                "Confirma que o link tem vídeo e não é só imagem ou texto.\n"
                "Se for conteúdo adulto, verifique se os cookies do X estão configurados."
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
        try:
            if file_path:
                if file_path.exists():
                    file_path.unlink()
                for p in file_path.parent.glob(f"{file_path.stem}_part*.mp4"):
                    p.unlink()
                logging.info("Limpeza de arquivos concluída para: %s", file_path.stem)
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

    asyncio.create_task(handle_text(int(chat_id), text))

    return {"status": "ok"}
