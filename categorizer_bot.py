import os
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =========================
# CONFIGURAÇÃO DE CREDENCIAIS
# =========================
# Pegando das variáveis de ambiente (Railway)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

if not all([API_ID, API_HASH, BOT_TOKEN, SESSION_STRING]):
    logging.warning("Faltam credenciais! Verifique as variáveis de ambiente.")

# =========================
# CLIENTES TELEGRAM
# =========================
# O Bot (para conversar com você e mostrar os botões)
bot = TelegramClient("bot_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# O Userbot (para enviar arquivos grandes de até 2GB)
userbot = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# =========================
# ROTAS DO BOT
# =========================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply("Olá! Sou o bot de Ingestão de Vídeos.\n\nUse o comando `/id` dentro do Tópico do seu Grupo para eu te dizer qual é o ID daquele tópico e do grupo!")

@bot.on(events.NewMessage(pattern='/id'))
async def id_handler(event):
    chat_id = event.chat_id
    # Se for um supergrupo com tópicos (fórum), a mensagem terá um 'reply_to_msg_id' que corresponde ao ID do Tópico.
    topic_id = event.message.reply_to_msg_id
    
    msg = f"📍 **IDs Capturados**\n\n"
    msg += f"**Grupo ID:** `{chat_id}`\n"
    
    if event.is_group and topic_id:
        msg += f"**Tópico ID:** `{topic_id}`\n\n"
        msg += "Cole estes IDs nas configurações do seu código para direcionar o upload para cá."
    elif event.is_group:
        msg += "\nVocê não está dentro de um Tópico específico, ou este grupo não tem Tópicos habilitados (Modo Fórum)."
    else:
        msg += "\nIsso parece ser um chat privado, não um grupo."

    await event.reply(msg)


async def main():
    logging.info("Iniciando Bot e Userbot...")
    
    # Remover o Webhook antigo que o main.py estava usando
    if BOT_TOKEN:
        import httpx
        try:
            r = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook")
            logging.info(f"Webhook deletado: {r.json()}")
        except Exception as e:
            logging.error(f"Erro ao deletar webhook: {e}")

    # Inicia o userbot
    await userbot.start()
    logging.info("Userbot conectado com sucesso!")
    
    # Roda ambos até ser parado
    await bot.run_until_disconnected()

if __name__ == "__main__":
    if API_ID == 0:
        print("Crie um arquivo .env ou defina as variáveis de ambiente antes de rodar.")
    else:
        bot.loop.run_until_complete(main())
