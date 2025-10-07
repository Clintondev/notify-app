import requests

# --- Opção 1: ntfy.sh ---
# Tópico para receber notificações. Mantenha-o em segredo.
NTFY_TOPIC = "gemini-notify-r2d2-ax7b9"

# --- Opção 2: Telegram ---
# Preencha com seu token e chat_id
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# Define o método de notificação padrão
NOTIFICATION_METHOD = "ntfy"

def send_to_ntfy(message):
    if not NTFY_TOPIC:
        print("⚠️ NTFY_TOPIC não configurado em send_push.py")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8')
        )
        print(f"✅ Notificação enviada via ntfy.sh")
    except Exception as e:
        print(f"❌ Erro ao enviar para ntfy.sh: {e}")

def send_to_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Token/Chat ID do Telegram não configurado em send_push.py")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={'chat_id': TELEGRAM_CHAT_ID, 'text': message}
        )
        print(f"✅ Notificação enviada via Telegram")
    except Exception as e:
        print(f"❌ Erro ao enviar para o Telegram: {e}")

def send_notification(message):
    """Função principal que decide para onde enviar a notificação."""
    if NOTIFICATION_METHOD == "ntfy":
        send_to_ntfy(message)
    elif NOTIFICATION_METHOD == "telegram":
        send_to_telegram(message)
    else:
        print(f"❗️ Método de notificação desconhecido: {NOTIFICATION_METHOD}")
