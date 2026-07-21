import os
import asyncio
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# Coloque suas credenciais aqui apenas para rodar este script
API_ID = int(input("Digite o seu API_ID (número): ").strip())
API_HASH = input("Digite o seu API_HASH: ").strip()

async def main():
    print("Iniciando processo de login no Telegram...")
    # Usando StringSession vazia para criar uma nova
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    
    await client.start()
    
    # Após logar (inserir telefone e código), pega a string da sessão
    session_string = client.session.save()
    
    print("\n" + "="*50)
    print("LOGIN BEM SUCEDIDO!")
    print("Copie o texto gigante abaixo e cole na variável SESSION_STRING no Railway:")
    print("="*50)
    print(session_string)
    print("="*50)
    print("\nNão compartilhe este código com ninguém!")

if __name__ == "__main__":
    asyncio.run(main())
