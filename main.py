import time
import datetime
import threading
import requests
import yfinance as yf
import pandas as pd
from flask import Flask

# --- RENDER PORT DİNLEMESİ İÇİN WEB SUNUCUSU ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Dipper Nasdaq Scanner"

def run_flask():
    app.run(host='0.0.0.0', port=10000)

# --- TELEGRAM VE TARAMA MANTIĞI ---
TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA"
TELEGRAM_CHAT_ID = "7743041008"

bildirilenler = {}
gunluk_sinyaller = {} # Günlük kâr takibi için verileri saklar
rapor_gonderildi_bugun = False

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Hatasi: {e}")

def get_penny_stocks():
    try:
        url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt"
        df = pd.read_csv(url, sep="|")
        symbols = df[df['Test Stock'] == 'N']['Symbol'].tolist()
        return [s for s in symbols if isinstance(s, str) and len(s) <= 4]
    except Exception as e:
        print(f"Liste alinirken hata: {e}")
        return []

def gun_sonu_raporu_gonder():
    """Borsa kapanışında (TSİ 23:00) günlük kâr performans raporu atar."""
    global gunluk_sinyaller
    if not gunluk_sinyaller:
        send_telegram_msg("📊 **GÜN SONU RAPORU:** Bugün kriterlere uyan sinyal oluşmadı.")
        return

    rapor = "📊 **GÜNÜN MİDAS / NASDAQ PERFORMANS ÖZETİ**\n\n"
    toplam_kar = 0

    for symbol, data in gunluk_sinyaller.items():
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="1m")
            
            entry = data['entry']
            kapanis = df['Close'].iloc[-1] if not df.empty else entry
            zirve = df['High'].max() if not df.empty else entry
            
            max_kar = ((zirve - entry) / entry) * 100
            kapanis_kar = ((kapanis - entry) / entry) * 100
            toplam_kar += max_kar

            rapor += (
                f"🔹 **#{symbol}**\n"
                f"  • Kırılım Fiyatı: ${entry:.2f}\n"
                f"  • Gün İçi Zirve: ${zirve:.2f} (🚀 **%{max_kar:.1f} Max Kâr**)\n"
                f"  • Kapanış: ${kapanis:.2f} (%{kapanis_kar:.1f})\n\n"
            )
        except Exception:
            continue

    ort_kar = toplam_kar / len(gunluk_sinyaller) if gunluk_sinyaller else 0
    rapor += f"🎯 **Ortalama Max Potansiyel:** %{ort_kar:.1f}\n"
    rapor += "_________________________________\n"
    rapor += "💡 *Kâr hesaplamaları kırılım anındaki direnç fiyatı baz alınmıştır.*"

    send_telegram_msg(rapor)
    gunluk_sinyaller.clear() # Gün bitti, listeyi sıfırla

def canli_kesintisiz_tarama():

    global rapor_gonderildi_bugun
    # ... kodun geri kalanı aynen devam eder ...
    
    # Zaman Kontrolü (TSİ 23:00'da rapor gönderimi)
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3) # TSİ (UTC+3)
    if now.hour == 23 and now.minute == 0:
        if not rapor_gonderildi_bugun:
            gun_sonu_raporu_gonder()
            rapor_gonderildi_bugun = True
    elif now.hour == 0:
        rapor_gonderildi_bugun = False # Gece yarısı resetle

    symbols = get_penny_stocks()
    if not symbols:
        return

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="1m")

            if df.empty or len(df) < 15:
                continue

            last_price = df['Close'].iloc[-1]

            if last_price >= 4.00 or last_price <= 0.05:
                continue

            last_volume = df['Volume'].iloc[-1]
            last_low = df['Low'].iloc[-1]
            
            resistance = df['High'].iloc[-16:-1].max()
            avg_volume = df['Volume'].iloc[-16:-1].mean()

            if avg_volume < 3000:
                continue

            is_breakout = last_price > resistance
            is_volume_confirm = last_volume > (avg_volume * 1.5)

            if is_breakout and is_volume_confirm:
                if symbol not in bildirilenler or (time.time() - bildirilenler[symbol]) > 60:
                    msg = (
                        f"⚡ **KESİNTİSİZ CANLI ALARM: #{symbol}**\n\n"
                        f"💵 **Anlık Fiyat:** ${last_price:.2f}\n"
                        f"🎯 **Kırılan Direnç:** ${resistance:.2f}\n"
                        f"🚀 **Hacim Sıçraması:** Ortalamanin {last_volume/avg_volume:.1f}x katı!\n"
                        f"🛡️ **Stop Level:** ${last_low:.2f}\n\n"
                        f"⚠️ *Midas'tan anında kontrol et!*"
                    )
                    send_telegram_msg(msg)
                    bildirilenler[symbol] = time.time()
                    
                    # Günlük rapora kaydet
                    if symbol not in gunluk_sinyaller:
                        gunluk_sinyaller[symbol] = {'entry': last_price}

        except Exception:
            continue

def start_scanner_loop():
    send_telegram_msg("🚀 **Canlı NASDAQ Taraması Aktif!**")
    while True:
        canli_kesintisiz_tarama()

if __name__ == '__main__':
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    run_flask()
