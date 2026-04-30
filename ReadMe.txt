# Bot de Download de Vídeos para Telegram (TikTok, Instagram e X)

Este repositório contém um bot para **Telegram** que permite aos usuários baixar vídeos do **TikTok**, **Instagram (reels/posts)** e **X (Twitter)**.  
O bot valida os links enviados, realiza o download automaticamente e envia o vídeo diretamente no chat do usuário.

Após a entrega, o bot remove os arquivos temporários do servidor e apaga as mensagens de progresso, mantendo a conversa limpa.

---

## Funcionalidades

- **Validação de Links**  
  Aceita apenas links válidos das plataformas suportadas (TikTok, Instagram e X).

- **Download Automático**  
  Realiza o download dos vídeos utilizando a biblioteca `yt-dlp`.

- **Envio pelo Telegram**  
  Envia o vídeo diretamente no chat do usuário usando a API oficial do Telegram.

- **Gestão de Armazenamento**  
  Remove automaticamente os arquivos baixados após o envio para evitar consumo excessivo de espaço.

- **Mensagens de Progresso Inteligentes**  
  Exibe mensagens como:
  - Link detectado  
  - Baixando vídeo  
  - Enviando vídeo  

  Todas essas mensagens são apagadas automaticamente após a entrega do vídeo.

- **Suporte a Cookies (Opcional)**  
  Permite o uso de cookies do Instagram e X para aumentar a taxa de sucesso em conteúdos que exigem login.

- **Comandos de Ajuda**  
  Responde aos comandos `/start` e `/help`.

---

## Plataformas Suportadas

- TikTok   
- Instagram  (reels e posts)  
- X / Twitter 

---

## Tecnologias Utilizadas

- **Python** – Linguagem principal do projeto  
- **FastAPI** – Servidor web para receber webhooks do Telegram  
- **yt-dlp** – Download de vídeos das plataformas suportadas  
- **Requests** – Comunicação com a API do Telegram  
- **Docker + ffmpeg** – Suporte à mesclagem de áudio e vídeo  
- **Railway** – Hospedagem do bot em produção  

---

## Configuração do Ambiente

### Dependências

As dependências do projeto estão listadas no arquivo `requirements.txt`:

```txt
fastapi
uvicorn
requests
yt-dlp
python-multipart
