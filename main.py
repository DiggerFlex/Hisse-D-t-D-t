import time
import requests
import pandas as pd
import yfinance as yf

TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA"
TELEGRAM_CHAT_ID = "7743041008"

bildirilen_hisseler = {}

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Hatasi: {e}")

def get_nasdaq_symbols():
    """NASDAQ'taki tum aktif hisse sembollerini resmi sunucudan anlik ceker"""
    try:
        url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt"
        df = pd.read_csv(url, sep="|")
        # Test/Gereksiz sembolleri temizle
        symbols = df[df['Test Stock'] == 'N']['Symbol'].tolist()
        print(f"Toplam {len(symbols)} NASDAQ hissesi listelendi.")
        return symbols
    except Exception as e:
        print(f"Hisse listesi cekilemedi: {e}")
        # Hata durumunda en populer ana hisselere yedeklen
        return ["AAPL", "NVDA", "TSLA", "AMD", "AMZN", "MSFT", "GOOGL", "META", "NFLX"]

def tarama_yap():
    symbols = get_nasdaq_symbols()
    send_telegram_msg(f"🔍 **Yeni Tarama Basladi!** Toplam {len(symbols)} NASDAQ hissesi taraniyor...")

    # Yfinance ban yememek icin hisseleri gruplar halinde tara
    for symbol in symbols:
        try:
            # Anlik verileri al (5 gunluk, 5 dakikalik mumlar)
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="5d", interval="5m")

            if df.empty or len(df) < 20:
                continue

            # Mum ve Direnc Analizi
            last_close = df['Close'].iloc[-1]
            last_volume = df['Volume'].iloc[-1]
            last_low = df['Low'].iloc[-1]
            
            resistance = df['High'].iloc[-21:-1].max() # Son 20 mum direnci
            avg_volume = df['Volume'].iloc[-21:-1].mean() # Son 20 mum hacim ortalamasi

            # Sadece hacimli hisseleri dikkate al (Çöp hisse filtresi)
            if avg_volume < 10000:
                continue

            # STRATEJI KONTROLU (Gorsellerdeki Kural)
            is_breakout = last_close > resistance
            is_volume_confirm = last_volume > (avg_volume * 1.5)

            if is_breakout and is_volume_confirm:
                # 1 saat icinde ayni hisse icin tekrar mesaj atma
                if symbol not in bildirilen_hisseler or (time.time() - bildirilen_hisseler[symbol]) > 3600:
                    msg = (
                        f"🚀 **NASDAQ KIRILIM VE HACIM ALARMI: #{symbol}**\n\n"
                        f"🔹 **Giris Fiyati:** ${last_close:.2f}\n"
                        f"🔹 **Kirilan Direnc:** ${resistance:.2f}\n"
                        f"📊 **Hacim Patlamasi:** Ortalamanin {last_volume/avg_volume:.1f}x katı!\n"
                        f"🛡️ **Stop-Loss:** ${last_low:.2f}\n\n"
                        f"⚠️ *Midas'tan kontrol edip isleme girebilirsin!*"
                    )
                    send_telegram_msg(msg)
                    bildirilen_hisseler[symbol] = time.time()
                    
        except Exception as e:
            continue

if __name__ == '__main__':
    send_telegram_msg("🤖 **TÜM NASDAQ Hisse Tarayicisi Tam Kapasite Baslatildi!**")
    while True:
        tarama_yap()
        time.sleep(300) # Her döngü bitiminde 5 dakika bekle
