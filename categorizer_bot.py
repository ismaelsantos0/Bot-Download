import os
import asyncio
import logging
import base64
import re
import subprocess
from pathlib import Path
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo
import yt_dlp

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =========================
# CONFIGURAÇÃO DE CREDENCIAIS
# =========================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING]):
    logging.warning("Faltam credenciais! Verifique as variáveis de ambiente.")

# =========================
# CONFIGURAÇÃO DO GRUPO
# =========================
GROUP_ID = -1003918499221
CATEGORIES = {
    "Longos": 4,
    "Privados": 30,
    "Menage": 8,
    "Diversos": 264,
    "Amadores": 2,
    "Cuckold": 7,
    "POV": 5,
    "Vazados": 3
}

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# CONFIGURAÇÃO DE COOKIES
# =========================
IG_COOKIES_B64 = (os.environ.get("IG_COOKIES_B64") or "").strip()
X_COOKIES_B64 = (os.environ.get("X_COOKIES_B64") or "").strip()
IG_COOKIE_PATH = Path("cookies_ig.txt")
X_COOKIE_PATH = Path("cookies_x.txt")

def _write_cookiefile(b64_value: str, path: Path, label: str):
    if not b64_value:
        logging.warning(f"{label}: Cookies não configurados.")
        return
    try:
        path.write_bytes(base64.b64decode(b64_value))
        logging.info(f"{label} cookies carregados.")
    except Exception as e:
        logging.error(f"Falha ao criar cookie {label}: {e}")

_write_cookiefile(IG_COOKIES_B64, IG_COOKIE_PATH, "Instagram")
_write_cookiefile(X_COOKIES_B64, X_COOKIE_PATH, "X/Twitter")

def _cookiefile_for(link: str) -> str | None:
    link = link.lower()
    if "instagram.com" in link and IG_COOKIE_PATH.exists():
        return str(IG_COOKIE_PATH)
    if ("x.com" in link or "twitter.com" in link) and X_COOKIE_PATH.exists():
        return str(X_COOKIE_PATH)
    return None

# =========================
# CLIENTES TELEGRAM
# =========================
bot = TelegramClient("bot_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
userbot = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Armazena temporariamente o link enviado por cada usuário
user_pending_links = {}

# =========================
# ROTAS DO BOT
# =========================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply("Olá! Sou o bot de Ingestão de Vídeos.\n\nEnvie qualquer link de vídeo (TikTok, Instagram, Twitter, etc) e eu vou baixar e categorizar para você!")

@bot.on(events.NewMessage(pattern='/id'))
async def id_handler(event):
    chat_id = event.chat_id
    topic_id = event.message.reply_to_msg_id
    
    msg = f"📍 **IDs Capturados**\n\n**Grupo ID:** `{chat_id}`\n"
    if event.is_group and topic_id:
        msg += f"**Tópico ID:** `{topic_id}`\n"
    await event.reply(msg)

@bot.on(events.NewMessage(pattern='(?i)^https?://.*'))
async def link_handler(event):
    # Quando o usuário envia um link, salvamos e mostramos os botões
    url = event.text.strip()
    user_pending_links[event.sender_id] = {"url": url, "topic_id": None}
    
    buttons = []
    row = []
    for name, topic_id in CATEGORIES.items():
        row.append(Button.inline(name, data=f"cat_{topic_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
        
    await event.reply("🔗 **Link recebido!**\nEscolha a categoria para enviar o vídeo:", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b'^cat_(.*)'))
async def category_callback(event):
    topic_id = int(event.data.decode().split('_')[1])
    user_id = event.sender_id
    
    if user_id not in user_pending_links:
        await event.answer("Nenhum link pendente! Envie o link novamente.", alert=True)
        return
        
    # Salva a categoria escolhida e pergunta sobre o título
    user_pending_links[user_id]["topic_id"] = topic_id
    cat_name = next((name for name, tid in CATEGORIES.items() if tid == topic_id), "Desconhecida")
    
    buttons = [
        [Button.inline("Com Título", data="title_yes"), Button.inline("Sem Título", data="title_no")]
    ]
    
    await event.edit(f"Categoria **{cat_name}** selecionada.\n\nDeseja que o vídeo seja enviado com o título original na legenda?", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b'^title_(.*)'))
async def title_callback(event):
    choice = event.data.decode().split('_')[1]
    user_id = event.sender_id
    
    if user_id not in user_pending_links or user_pending_links[user_id].get("topic_id") is None:
        await event.answer("Processo expirado. Envie o link novamente.", alert=True)
        return
        
    data = user_pending_links.pop(user_id)
    url = data["url"]
    topic_id = data["topic_id"]
    
    cat_name = next((name for name, tid in CATEGORIES.items() if tid == topic_id), "Desconhecida")
    
    msg = await event.edit(f"⏳ **Baixando vídeo** da categoria **{cat_name}**...\nIsso pode demorar um pouco dependendo do tamanho.")
    
    try:
        # Configuração do yt-dlp
        ydl_opts = {
            'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
        }
        
        # Adiciona cookies se for Twitter ou Instagram
        cf = _cookiefile_for(url)
        if cf:
            ydl_opts['cookiefile'] = cf
        
        # Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, url, download=True)
            # Tenta descobrir o nome final do arquivo
            file_path = Path(ydl.prepare_filename(info))
            if not file_path.exists():
                file_path = file_path.with_suffix('.mp4')
                
        if not file_path.exists():
            raise Exception("O vídeo não pôde ser salvo corretamente.")

        tamanho_mb = file_path.stat().st_size / (1024 * 1024)
        await msg.edit(f"🚀 **Download concluído!** ({tamanho_mb:.1f} MB)\nGerando miniatura e enviando...")
        
        # Extrair metadados do yt-dlp
        duration = int(info.get('duration') or 0)
        width = int(info.get('width') or 0)
        height = int(info.get('height') or 0)
        
        # Gerar miniatura (thumbnail) usando ffmpeg
        thumb_path = file_path.with_suffix('.jpg')
        # Tenta pegar frame no segundo 1
        subprocess.run(['ffmpeg', '-y', '-i', str(file_path), '-ss', '00:00:01.000', '-vframes', '1', str(thumb_path)], capture_output=True)
        if not thumb_path.exists():
            # Tenta pegar o primeiro frame se o vídeo for muito curto
            subprocess.run(['ffmpeg', '-y', '-i', str(file_path), '-vframes', '1', str(thumb_path)], capture_output=True)

        # Tenta extrair o título do vídeo e resumir
        title = info.get('title', '').strip()
        if len(title) > 60:
            title = title[:57] + "..."
            
        caption_text = f"🎬 **{title}**" if (title and choice == "yes") else ""

        # Upload com o Userbot passando os atributos corretos
        await userbot.send_file(
            GROUP_ID, 
            file=file_path, 
            reply_to=topic_id,
            caption=caption_text,
            thumb=str(thumb_path) if thumb_path.exists() else None,
            attributes=[DocumentAttributeVideo(
                duration=duration,
                w=width,
                h=height,
                supports_streaming=True
            )]
        )
        
        await msg.edit(f"✅ **Sucesso!**\nVídeo de {tamanho_mb:.1f} MB enviado para o tópico **{cat_name}**.")
        
        # Limpeza
        if file_path.exists():
            file_path.unlink()
        if thumb_path.exists():
            thumb_path.unlink()
            
    except Exception as e:
        logging.error(f"Erro no processamento: {e}")
        await msg.edit(f"❌ **Ocorreu um erro.**\n\nDetalhes: `{e}`")


async def main():
    logging.info("Iniciando Bot e Userbot...")
    
    # Remover o Webhook antigo
    if BOT_TOKEN:
        import httpx
        try:
            r = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
            logging.info(f"Webhook deletado: {r.json()}")
        except Exception as e:
            logging.error(f"Erro ao deletar webhook: {e}")

    await userbot.start()
    logging.info("Userbot conectado com sucesso!")
    
    await bot.run_until_disconnected()

if __name__ == "__main__":
    if API_ID == 0:
        print("Faltam variáveis de ambiente!")
    else:
        bot.loop.run_until_complete(main())
