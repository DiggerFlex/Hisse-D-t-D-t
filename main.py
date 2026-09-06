import os
from flask import Flask, request
import requests

app = Flask(__name__)

# Kendi bilgilerini tırnakların içine yaz
TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA"
TELEGRAM_CHAT_ID = "7743041008"

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Hata: {e}")

@app.route('/')
def home():
    return "Bot Calisiyor!"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if data:
        msg = (
            f"🚀 **KIRILIM VE HACİM ALARMI: #{data.get('ticker')}**\n\n"
            f"🔹 **Giriş Fiyatı (Kapanış):** ${data.get('price')}\n"
            f"🔹 **Kırılan Direnç:** ${data.get('resistance')}\n"
            f"🛡️ **Önerilen Stop-Loss:** ${data.get('stop')}\n\n"
            f"⚠️ *Midas'tan kontrol edip işleme girebilirsin!*"
        )
        send_telegram_msg(msg)
        return "OK", 200
    return "No Data", 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
