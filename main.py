import time
import datetime
import threading
import requests
import yfinance as yf
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "NASDAQ Scanner Active"

def run_flask():
    app.run(host='0.0.0.0', port=10000)

TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA                                               "
TELEGRAM_CHAT_ID = "7743041008"

bildirilenler = {}
gunluk_sinyaller = {}
rapor_gonderildi_bugun = False

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
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

def kirilim_analizi_yap(df, resistance, avg_volume):
    """Mum yapısı ve hacme göre yüzdelik risk skoru hesaplar."""
    last_candle = df.iloc[-1]
    
    close_p = last_candle['Close']
    open_p = last_candle['Open']
    high_p = last_candle['High']
    low_p = last_candle['Low']
    volume = last_candle['Volume']
    
    body = abs(close_p - open_p)
    candle_range = high_p - low_p if (high_p - low_p) > 0 else 0.01
    upper_wick = high_p - max(open_p, close_p)
    
    vol_ratio = volume / avg_volume if avg_volume > 0 else 1.0

    # 1. YÜKSEK RİSK (%70 - %90 Risk / Fake Kırılım Şüphesi)
    if upper_wick > body or close_p < open_p or vol_ratio < 1.5:
        risk_pct = 85 if upper_wick > (body * 2) else 70
        return f"🔴 %{risk_pct} RİSK (Fake Kırılım Eğilimi)", "İğnesi uzun/Gövde zayıf veya hacim cılız."

    # 2. ORTA RİSK (%40 - %60 Risk)
    elif 1.5 <= vol_ratio < 2.2:
        return "🟡 %50 RİSK (Yavaş Hacimli Kırılım)", "Kırılım var ancak hacim desteği orta seviyede."

    # 3. DÜŞÜK RİSK (%10 - %30 Risk / Onaylı Kırılım)
    else:
        if close_p > open_p and (body / candle_range) > 0.5:
            risk_pct = 15 if vol_ratio >= 3.0 else 25
            return f"🟢 %{risk_pct} RİSK (Gerçek / Onaylı Kırılım)", "Dolgun yeşil mum ve güçlü hacim onayı!"
        return "🟡 %40 RİSK (Standart Kırılım)", "Direnç üzeri kapanış mevcut."

def process_symbol(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")

        if df.empty or len(df) < 20:
            return

        last_price = df['Close'].iloc[-1]

        # Fiyat Filtresi: $0.05 - $10.00
        if last_price >= 10.00 or last_price <= 0.05:
            return

        last_volume = df['Volume'].iloc[-1]
        last_low = df['Low'].iloc[-1]
        
        resistance = df['High'][:-1].max() 
        avg_volume = df['Volume'][:-1].mean()

        if avg_volume < 1000:
            return

        if last_price > resistance:
            if symbol not in bildirilenler or (time.time() - bildirilenler[symbol]) > 60:
                
                risk_durumu, aciklama = kirilim_analizi_yap(df, resistance, avg_volume)
                vol_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0
                
                # TradingView Hızlı Grafik Bağlantısı
                tv_url = f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"

                msg = (
                    f"⚡ **CANLI NASDAQ ALARMI: #{symbol}**\n\n"
                    f"📊 **Risk Profili:** {risk_durumu}\n"
                    f"📝 **Analiz:** {aciklama}\n\n"
                    f"💵 **Anlık Fiyat:** ${last_price:.2f}\n"
                    f"🎯 **Günün Zirvesi (Direnç):** ${resistance:.2f}\n"
                    f"📈 **Hacim Sıçraması:** {vol_ratio:.1f}x katı\n"
                    f"🛡️ **Zarar Kes (Stop):** ${last_low:.2f}\n\n"
                    f"🔗 [TradingView'de Grafiği Aç]({tv_url})\n"
                    f"⚠️ *Midas'tan mumu ve hacmi kontrol et!*"
                )
                send_telegram_msg(msg)
                bildirilenler[symbol] = time.time()
                
                if symbol not in gunluk_sinyaller:
                    gunluk_sinyaller[symbol] = {'entry': last_price}

    except Exception:
        pass

def gun_sonu_raporu_gonder():
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
    gunluk_sinyaller.clear()

def canli_kesintisiz_tarama():
    global rapor_gonderildi_bugun
    
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3) # TSİ
    
    # Gün Sonu Raporu Kontrolü (TSİ 23:00)
    if now.hour == 23 and now.minute == 0:
        if not rapor_gonderildi_bugun:
            gun_sonu_raporu_gonder()
            rapor_gonderildi_bugun = True
    elif now.hour == 0:
        rapor_gonderildi_bugun = False

    symbols = get_penny_stocks()
    if not symbols:
        return

    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(process_symbol, symbols)

def start_scanner_loop():
    send_telegram_msg("🚀 **Nasdaq Scanner Aktif!**")
    while True:
        canli_kesintisiz_tarama()

if __name__ == '__main__':
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    run_flask()
