import time
import requests
import yfinance as yf
import pandas as pd

TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA"
TELEGRAM_CHAT_ID = "7743041008"

bildirilenler = {}

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Hatasi: {e}")

def get_penny_stocks():
    """NASDAQ verisinden $4 alti hisse sembollerini anlik getirir"""
    try:
        url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt"
        df = pd.read_csv(url, sep="|")
        symbols = df[df['Test Stock'] == 'N']['Symbol'].tolist()
        return [s for s in symbols if isinstance(s, str) and len(s) <= 4]
    except Exception as e:
        print(f"Liste alinirken hata: {e}")
        return []

def canli_kesintisiz_tarama():
    symbols = get_penny_stocks()
    if not symbols:
        return

    # Hisseleri anlik sirayla tara
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            # Son 1 gunluk 1 dakikalik anlik mum verisi
            df = ticker.history(period="1d", interval="1m")

            if df.empty or len(df) < 15:
                continue

            last_price = df['Close'].iloc[-1]

            # 🛑 SADECE $4 ALTI PENNY STOCK FİLTRESİ
            if last_price >= 4.00 or last_price <= 0.05:
                continue

            last_volume = df['Volume'].iloc[-1]
            last_low = df['Low'].iloc[-1]
            
            resistance = df['High'].iloc[-16:-1].max() # Son 15 dakikalik direnç
            avg_volume = df['Volume'].iloc[-16:-1].mean() # Son 15 dakikalik hacim ortalamasi

            if avg_volume < 3000: # Sıfır hacimli ölü hisseleri pas geç
                continue

            # STRATEJİ: Direnç Kırılımı + Hacim Patlaması (>= 1.5x)
            is_breakout = last_price > resistance
            is_volume_confirm = last_volume > (avg_volume * 1.5)

            if is_breakout and is_volume_confirm:
                # 15 dakika içinde aynı hisse için tekrar mesaj atıp spam yapmaz
                if symbol not in bildirilenler or (time.time() - bildirilenler[symbol]) > 900:
                    msg = (
                        f"⚡ **KESİNTİSİZ CANLI ALARM: #{symbol}**\n\n"
                        f"💵 **Anlık Fiyat:** ${last_price:.2f}\n"
                        f"🎯 **Kırılan Direnç:** ${resistance:.2f}\n"
                        f"📊 **Hacim Sıçraması:** Ortalamanin {last_volume/avg_volume:.1f}x katı!\n"
                        f"🛡️ **Stop Level:** ${last_low:.2f}\n\n"
                        f"⚠️ *Midas'tan anında kontrol et!*"
                    )
                    send_telegram_msg(msg)
                    bildirilenler[symbol] = time.time()

        except Exception:
            continue

if __name__ == '__main__':
    send_telegram_msg("🚀 **Canlı NASDAQ Taraması Başlatıldı!**")
    
    # KESİNTİSİZ SONSUZ DÖNGÜ (Durdurulamaz Tarama)
    while True:
        canli_kesintisiz_tarama()
