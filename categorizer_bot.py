import os
import asyncio
import logging
from pathlib import Path
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
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
    user_pending_links[event.sender_id] = url
    
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
        
    url = user_pending_links.pop(user_id)
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
        await msg.edit(f"🚀 **Download concluído!** ({tamanho_mb:.1f} MB)\nFazendo upload pelo Userbot...")
        
        # Upload com o Userbot para evitar o limite de 50MB
        await userbot.send_file(
            GROUP_ID, 
            file=file_path, 
            reply_to=topic_id,
            caption=f"📂 **{cat_name}**"
        )
        
        await msg.edit(f"✅ **Sucesso!**\nVídeo de {tamanho_mb:.1f} MB enviado para o tópico **{cat_name}**.")
        
        # Limpeza
        if file_path.exists():
            file_path.unlink()
            
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
