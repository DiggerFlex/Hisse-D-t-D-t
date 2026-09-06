import time
import datetime
import threading
import requests
import feedparser
import yfinance as yf
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

# ==========================================
# 1. RENDER UYANIK TUTMA (FLASK SUNUCUSU)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "NASDAQ Scanner & News Tracker Active"

def run_flask():
    app.run(host='0.0.0.0', port=10000)


# ==========================================
# 2. AYARLAR VE DİNAMİK DEĞİŞKENLER
# ==========================================
# Telegram API Bilgilerin
TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA"
TELEGRAM_CHAT_ID = "7743041008"

# Telegram /limit komutuyla anlık değiştirilebilir fiyat limiti (Varsayılan: $3.50)
MAX_PRICE_LIMIT = 3.50

bildirilenler = {}          # Hisselere sürekli üst üste alarm atmamak için zaman kaydı
gunluk_sinyaller = {}       # Gün sonu performans raporu için sinyal kaydı
gonderilen_haberler = set() # Tekrar haber atmamak için haber hafızası
rapor_gonderildi_bugun = False
last_update_id = 0          # Telegram komut takibi için mesaj kimliği


# ==========================================
# 3. TELEGRAM İLETİŞİM FONKSİYONLARI
# ==========================================
def send_telegram_msg(message):
    """Telegram API üzerinden belirlenen kanala Markdown formatında mesaj atar."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload)
        if res.status_code != 200:
            print(f"Telegram Hatasi: {res.text}")
    except Exception as e:
        print(f"Telegram Baglanti Hatasi: {e}")

def check_telegram_commands():
    """Telegram'dan gelen /limit komutlarını anlık dinler."""
    global MAX_PRICE_LIMIT, last_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    try:
        res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 2}).json()
        if "result" in res:
            for update in res["result"]:
                last_update_id = update["update_id"]
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].strip()
                    
                    if text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 1:
                            send_telegram_msg(f"ℹ️ **Mevcut Üst Fiyat Limiti:** ${MAX_PRICE_LIMIT:.2f}")
                        elif len(parts) == 2:
                            try:
                                new_limit = float(parts[1])
                                if 0.1 <= new_limit <= 20.0:
                                    MAX_PRICE_LIMIT = new_limit
                                    send_telegram_msg(f"✅ **Fiyat limiti başarıyla güncellendi:** ${MAX_PRICE_LIMIT:.2f}")
                                else:
                                    send_telegram_msg("⚠️ Lütfen $0.10 ile $20.00 arasında bir değer girin.")
                            except ValueError:
                                send_telegram_msg("⚠️ Geçersiz format! Örnek kullanım: `/limit 3.5` veya `/limit 5`")
    except Exception:
        pass


# ==========================================
# 4. BORSADAN HİSSE LİSTESİ ÇEKME
# ==========================================
def get_penny_stocks():
    """NASDAQ FTP sunucusundan tüm aktif listelenmiş hisse sembollerini çeker."""
    try:
        url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt"
        df = pd.read_csv(url, sep="|")
        symbols = df[df['Test Stock'] == 'N']['Symbol'].tolist()
        return [s for s in symbols if isinstance(s, str) and len(s) <= 4]
    except Exception as e:
        print(f"Liste alinirken hata: {e}")
        return []


# ==========================================
# 5. KIRILIM VE FAKEOUT ANALİZİ (YENİ SİNAN ETİKETLERİ)
# ==========================================
def kirilim_analizi_yap(df, resistance, avg_volume):
    """Son mumun gövde yapısını ve hacmini analiz ederek sahte kırılımları eler."""
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

    # Sahte Kırılım (Tuzak)
    if upper_wick > body or close_p < open_p or vol_ratio < 2.0:
        return "🔴 Fake Kırılım", "Cılız hacim veya uzun üst iğne! UZAK DUR."
    
    # En Kaliteli Kırılım
    if close_p > open_p and (body / candle_range) > 0.6 and vol_ratio >= 3.0:
        return "🔥 İyi Kırılım", "Mükemmel dolgun mum ve devasa hacim!"
    
    return "🟡 Normal Kırılım", "Direnç üzeri kapanış ve yeterli hacim."


# ==========================================
# 6. HİSSE BAZLI CANLI FİLTRELEME & ALARM
# ==========================================
def process_symbol(symbol):
    """Tek bir hisse için fiyatı, hacmi, stop seviyesini ve hedefleri hesaplar."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")

        if df.empty or len(df) < 20:
            return

        last_price = df['Close'].iloc[-1]

        # Dinamik Fiyat Filtresi ($0.05 ile MAX_PRICE_LIMIT arası)
        if last_price >= MAX_PRICE_LIMIT or last_price <= 0.05:
            return

        last_volume = df['Volume'].iloc[-1]
        resistance = df['High'][:-1].max()
        avg_volume = df['Volume'][:-1].mean()

        if avg_volume < 1000:
            return

        if last_price > resistance:
            risk_durumu, aciklama = kirilim_analizi_yap(df, resistance, avg_volume)
            
            # Fake Kırılımları bildirme
            if "Fake" in risk_durumu:
                return

            if symbol not in bildirilenler or (time.time() - bildirilenler[symbol]) > 60:
                vol_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0
                
                # Sıkı Stop ve 2 Kademeli Satış Hesaplaması
                tight_stop = resistance * 0.98   # -%2.0 Stop
                tp1 = resistance * 1.05         # 1. Kademe Satış (+%5.0)
                tp2 = resistance * 1.10         # 2. Kademe Satış (+%10.0)

                tv_url = f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"

                # İstenen sıralamada güncellenmiş şablon
                msg = (
                    f"⚡ **NASDAQ ALARMI: #{symbol}**\n\n"
                    f"📊 **Sinyal Durumu:** {risk_durumu}\n"
                    f"📝 **Analiz:** {aciklama}\n\n"
                    f"💵 **Giriş / Kırılım:** ${resistance:.2f}\n"
                    f"🛡️ **Stop (-%2.0):** ${tight_stop:.2f}\n"
                    f"📈 **Hacim Gücü:** {vol_ratio:.1f}x katı\n\n"
                    f"🎯 **1. Kademe Satış (+%5.0):** ${tp1:.2f}\n"
                    f"🎯 **2. Kademe Satış (+%10.0):** ${tp2:.2f}\n\n"
                    f"🔥 **MOTİVASYON:** Obez olma !\n\n"
                    f"🔗 [TradingView'de Grafiği Aç]({tv_url})"
                )
                send_telegram_msg(msg)
                bildirilenler[symbol] = time.time()
                
                if symbol not in gunluk_sinyaller:
                    gunluk_sinyaller[symbol] = {'entry': last_price}

    except Exception:
        pass


# ==========================================
# 7. TRUMP VE PİYASA AÇIKLAMA MODÜLÜ
# ==========================================
def trump_ve_piyasa_haberleri_kontrol_et():
    """Trump veya NASDAQ'ı etkileyecek kritik açıklamaları bağımsız mesaj olarak atar."""
    global gonderilen_haberler
    rss_url = "https://news.google.com/rss/search?q=Trump+NASDAQ+or+Stock+Market&hl=en-US&gl=US&ceid=US:en"
    
    try:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:3]:
            haber_id = entry.link
            
            if haber_id not in gonderilen_haberler:
                baslik = entry.title
                link = entry.link
                
                haber_mesaji = (
                    f"⚠️ **Trump Açıklama** ⚠️\n\n"
                    f"📢 **Başlık:** {baslik}\n\n"
                    f"🔗 **Detay/Link:** {link}"
                )
                
                send_telegram_msg(haber_mesaji)
                gonderilen_haberler.add(haber_id)
    except Exception as e:
        print(f"Haber akisi hatasi: {e}")

def haber_tarama_loop():
    while True:
        trump_ve_piyasa_haberleri_kontrol_et()
        time.sleep(120) # 2 dakikada bir kontrol eder


# ==========================================
# 8. GÜN SONU PERFORMANS RAPORU
# ==========================================
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
            
            if zirve <= entry:
                durum_str = "🛡️ **-%2.0 Stop Oldu**"
            else:
                durum_str = f"🚀 **%{max_kar:.1f} Max Kâr**"

            toplam_kar += max_kar

            rapor += (
                f"🔹 **#{symbol}**\n"
                f"  • Kırılım / Giriş: ${entry:.2f}\n"
                f"  • Gün İçi Zirve: ${zirve:.2f} ({durum_str})\n"
                f"  • Kapanış: ${kapanis:.2f} (%{kapanis_kar:.1f})\n\n"
            )
        except Exception:
            continue

    ort_kar = toplam_kar / len(gunluk_sinyaller) if gunluk_sinyaller else 0
    rapor += (
        f"🎯 **Günlük Ortalama Max Potansiyel:** %{ort_kar:.1f}\n"
        f"🔥 **Günün Tavsiyesi:** Disiplini koru, obez olma !"
    )
    send_telegram_msg(rapor)
    gunluk_sinyaller.clear()


# ==========================================
# 9. CANLI TARAMA VE PROGRAM BAŞLATICI
# ==========================================
def canli_kesintisiz_tarama():
    global rapor_gonderildi_bugun
    
    check_telegram_commands()

    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
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
    send_telegram_msg("🚀 **Nasdaq Scanner & Haber Modülü Aktif!**")
    while True:
        canli_kesintisiz_tarama()

if __name__ == '__main__':
    # Haber takip sistemini başlat
    threading.Thread(target=haber_tarama_loop, daemon=True).start()
    
    # Canlı hisse tarama sistemini başlat
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    
    # Flask sunucusunu başlat (Render uyanık tutma)
    run_flask()
