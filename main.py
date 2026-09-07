import time
import datetime
import threading
import requests
import feedparser
import yfinance as yf
import pandas as pd
import numpy as np
from dateutil import parser
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

# ==========================================
# 1. RENDER UYANIK TUTMA (FLASK SUNUCUSU)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "NASDAQ Scanner Active!"

def run_flask():
    app.run(host='0.0.0.0', port=10000)


# ==========================================
# 2. AYARLAR VE DİNAMİK DEĞİŞKENLER
# ==========================================
TELEGRAM_BOT_TOKEN = "8750813780:AAFCMXBLA1ZOsMUZz6vrSIJz5ccg94QMsdA"
TELEGRAM_CHAT_ID = "7743041008"
RENDER_DEPLOY_HOOK_URL = "https://api.render.com/deploy/srv-daemtan40ujc73ft425g?key=o1ghEoCwW10"

MAX_PRICE_LIMIT = 3.50
START_TIME = datetime.datetime.now()

bildirilenler = {}          
gunluk_sinyaller = {}       
gonderilen_haberler = set() 
rapor_gonderildi_bugun = False
last_update_id = 0          

# Görsel Bağlantıları
IMAGE_URLS = {
    "GERCEK_1": "https://i.imgur.com/40H3720.png",
    "GERCEK_2": "https://i.imgur.com/40H3720.png",
    "YAVAS_HACIM": "https://i.imgur.com/40H3720.png",
    "ONAYLI": "https://i.imgur.com/40H3720.png"
}


# ==========================================
# 3. TELEGRAM İLETİŞİM FONKSİYONLARI
# ==========================================
def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Baglanti Hatasi: {e}")

def send_telegram_side_photo(photo_url, caption):
    url_photo = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        # Tarayıcı gibi görünmek için Header eklendi (Imgur engelini aşar)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        img_response = requests.get(photo_url, headers=headers, timeout=15)
        
        if img_response.status_code == 200:
            files = {'photo': ('chart.png', img_response.content)}
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "Markdown"
            }
            res = requests.post(url_photo, data=payload, files=files, timeout=20).json()
            if not res.get("ok"):
                print(f"Fotoğraf gönderilemedi, hata: {res}")
                send_telegram_msg(caption)
        else:
            print(f"Fotoğraf indirilemedi, HTTP Kod: {img_response.status_code}")
            send_telegram_msg(caption)
    except Exception as e:
        print(f"Resim gonderme hatasi: {e}")
        send_telegram_msg(caption)

def check_telegram_commands():
    global MAX_PRICE_LIMIT, last_update_id
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    try:
        start_req = time.time()
        res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 2}).json()
        latency = (time.time() - start_req) * 1000
        
        if "result" in res:
            for update in res["result"]:
                last_update_id = update["update_id"]
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].strip()
                    
                    if text in ["/ping", "/pingms"]:
                        status_msg = (
                            f"⚡ **Sunucu Yanıt Süresi:** `{latency:.0f} ms`\n"
                            f"🟢 **Durum:** Aktif & Çalışıyor\n"
                            f"💵 **Mevcut Limit:** ${MAX_PRICE_LIMIT:.2f}"
                        )
                        send_telegram_msg(status_msg)

                    elif text == "/render":
                        if RENDER_DEPLOY_HOOK_URL:
                            send_telegram_msg("🔄 **Render Redeploy Tetiklendi!** Yeniden başlatılıyor...")
                            try:
                                requests.post(RENDER_DEPLOY_HOOK_URL, timeout=10)
                            except Exception as e:
                                send_telegram_msg(f"⚠️ Render tetikleme hatası: {e}")
                        else:
                            send_telegram_msg("⚠️ Render Deploy Hook URL tanımlı değil.")

                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 1:
                            send_telegram_msg(f"ℹ️ **Mevcut Üst Fiyat Limiti:** ${MAX_PRICE_LIMIT:.2f}")
                        elif len(parts) == 2:
                            try:
                                new_limit = float(parts[1])
                                if 0.1 <= new_limit <= 20.0:
                                    MAX_PRICE_LIMIT = new_limit
                                    send_telegram_msg(f"✅ **Fiyat limiti güncellendi:** ${MAX_PRICE_LIMIT:.2f}")
                                else:
                                    send_telegram_msg("⚠️ Lütfen $0.10 ile $20.00 arasında bir değer girin.")
                            except ValueError:
                                send_telegram_msg("⚠️ Geçersiz format! Örnek: `/limit 3.5`")
    except Exception:
        pass


# ==========================================
# 4. BORSADAN HİSSE LİSTESİ ÇEKME
# ==========================================
def get_penny_stocks():
    try:
        url = "ftp://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt"
        df = pd.read_csv(url, sep="|")
        symbols = df[df['Test Stock'] == 'N']['Symbol'].tolist()
        return [s for s in symbols if isinstance(s, str) and len(s) <= 4]
    except Exception as e:
        print(f"Liste alinirken hata: {e}")
        return []


# ==========================================
# 5. DİNAMİK HEDEF & KIRILIM TİPİ TESPİTİ
# ==========================================
def calculate_dynamic_targets(df, last_price):
    try:
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(14).mean().iloc[-1]

        if pd.isna(atr) or atr == 0:
            atr = last_price * 0.03

        tp1_dyn = max(last_price * 1.05, last_price + (1.5 * atr))
        tp1_pct = ((tp1_dyn - last_price) / last_price) * 100

        tp2_dyn = max(last_price * 1.15, last_price + (3.5 * atr))
        tp2_pct = ((tp2_dyn - last_price) / last_price) * 100

        return tp1_dyn, tp1_pct, tp2_dyn, tp2_pct
    except Exception:
        return last_price * 1.07, 7.0, last_price * 1.25, 25.0

def detect_breakout_type(df, vol_ratio, resistance, last_price):
    c_curr = df['Close'].iloc[-1]
    o_curr = df['Open'].iloc[-1]
    
    c_prev1 = df['Close'].iloc[-2]
    o_prev1 = df['Open'].iloc[-2]
    l_prev1 = df['Low'].iloc[-2]
    
    c_prev2 = df['Close'].iloc[-3]
    o_prev2 = df['Open'].iloc[-3]

    if c_prev2 > resistance and c_prev1 < o_prev1 and c_curr > o_curr:
        return "Onaylı Kırılım (Retest)", IMAGE_URLS["ONAYLI"]
    elif c_prev1 < o_prev1 and c_curr > o_curr and l_prev1 <= resistance:
        return "Gerçek Kırılım (Fitilli/Düzeltmeli)", IMAGE_URLS["GERCEK_2"]
    elif 1.8 <= vol_ratio < 2.5 and c_curr > o_curr:
        return "Yavaş Hacimli Kırılım", IMAGE_URLS["YAVAS_HACIM"]
    else:
        return "Gerçek Kırılım (Güçlü Dikine)", IMAGE_URLS["GERCEK_1"]


# ==========================================
# 6. HİSSE BAZLI CANLI FİLTRELEME & ALARM
# ==========================================
def process_symbol(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")

        if df.empty or len(df) < 20:
            return

        last_price = df['Close'].iloc[-1]

        if last_price >= MAX_PRICE_LIMIT or last_price <= 0.05:
            return

        last_volume = df['Volume'].iloc[-1]
        open_price = df['Open'].iloc[-1]
        
        resistance = df['High'][:-1].max()
        avg_volume = df['Volume'][:-1].mean()

        if avg_volume < 1000:
            return

        distance_to_resistance = (resistance - last_price) / resistance if resistance > 0 else 1.0
        vol_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0

        if last_price <= resistance and distance_to_resistance <= 0.015 and last_price > open_price:
            if vol_ratio >= 1.8:
                kirilim_adi, img_url = detect_breakout_type(df, vol_ratio, resistance, last_price)
            else:
                return

            if symbol not in bildirilenler or (time.time() - bildirilenler[symbol]) > 60:
                tight_stop = last_price * 0.98   
                tp1, tp1_pct, tp2, tp2_pct = calculate_dynamic_targets(df, last_price)
                tv_url = f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"

                msg = (
                    f"⚡ NASDAQ ALARMI: #{symbol}\n\n"
                    f"📊 Sinyal Durumu: 🟢 {kirilim_adi}\n"
                    f"📝 Analiz: Direnç kırıldı, kırılım türü fotoğraftaki yapı ile eşleşiyor.\n\n"
                    f"💵 Giriş / Kırılım: ${last_price:.2f}\n"
                    f"🛡️ Stop (-%2.0): ${tight_stop:.2f}\n\n"
                    f"📈 Hacim Gücü: {vol_ratio:.1f}x katı\n\n"
                    f"🎯 1. Kademe Satış (+%{tp1_pct:.1f}): ${tp1:.2f}\n"
                    f"🎯 2. Kademe Satış (+%{tp2_pct:.1f}): ${tp2:.2f}\n\n"
                    f"🔗 [TradingView'de Grafiği Aç]({tv_url})"
                )
                
                send_telegram_side_photo(img_url, msg)
                bildirilenler[symbol] = time.time()
                
                if symbol not in gunluk_sinyaller:
                    gunluk_sinyaller[symbol] = {'entry': last_price}
    except Exception:
        pass


# ==========================================
# 7. TRUMP VE HABER MODÜLÜ
# ==========================================
def kritik_piyasa_etkisi_analiz_et(metin):
    metin_lower = metin.lower()
    olumlu_kelimeler = ["cut tariffs", "tax cut", "trade deal", "peace", "agreement", "support", "boost", "surge"]
    olumsuz_kelimeler = ["war", "strike", "attack", "sanction", "tariff", "tariffs", "threat", "china", "russia", "ban"]

    if any(word in metin_lower for word in olumsuz_kelimeler):
        return "🚨 **NASDAQ Etkisi: Olumsuz**"
    elif any(word in metin_lower for word in olumlu_kelimeler):
        return "🚀 **NASDAQ Etkisi: Olumlu**"
    else:
        return "⚠️ **NASDAQ Etkisi: Riskli**"

def trump_ve_piyasa_haberleri_kontrol_et():
    global gonderilen_haberler
    rss_url = "https://news.google.com/rss/search?q=Trump+(war+OR+tariff+OR+sanction+OR+attack+OR+China+OR+strike)&hl=en-US&gl=US&ceid=US:en"
    kritik_kelimeler = ["war", "tariff", "tariffs", "sanction", "attack", "strike", "china", "russia", "military", "missile", "threat", "ban"]
    
    try:
        feed = feedparser.parse(rss_url)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        
        for entry in feed.entries[:5]:
            haber_id = entry.title
            baslik_lower = entry.title.lower()
            
            if haber_id in gonderilen_haberler:
                continue
                
            if hasattr(entry, 'published'):
                try:
                    pub_time = parser.parse(entry.published)
                    if pub_time.tzinfo is None:
                        pub_time = pub_time.replace(tzinfo=datetime.timezone.utc)
                    if (now_utc - pub_time).total_seconds() / 60.0 > 60:
                        gonderilen_haberler.add(haber_id)
                        continue
                except Exception:
                    pass

            if any(word in baslik_lower for word in kritik_kelimeler):
                etki = kritik_piyasa_etkisi_analiz_et(entry.title)
                send_telegram_msg(f"⚠️ **Trump Açıklama** ⚠️\n\n{etki}")
                gonderilen_haberler.add(haber_id)
    except Exception as e:
        print(f"Haber akisi hatasi: {e}")

def haber_tarama_loop():
    while True:
        trump_ve_piyasa_haberleri_kontrol_et()
        time.sleep(3600)


# ==========================================
# 8. GÜN SONU PERFORMANS RAPORU
# ==========================================
def gun_sonu_raporu_gonder():
    global gunluk_sinyaller
    if not gunluk_sinyaller:
        send_telegram_msg("📊 **GÜN SONU RAPORU:** Bugün kriterlere uyan kırılmalar oluşmadı.")
        return

    rapor = "📊 **GÜNÜN HİSSE PERFORMANS ÖZETİ**\n\n"
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
            
            durum_str = f"🛡️ **-%2.0 Stop Oldu**" if zirve <= entry else f"🚀 **%{max_kar:.1f} Max Kâr**"
            toplam_kar += max_kar

            rapor += (
                f"🔹 **#{symbol}**\n"
                f"  • Giriş: ${entry:.2f}\n"
                f"  • Zirve: ${zirve:.2f} ({durum_str})\n"
                f"  • Kapanış: ${kapanis:.2f} (%{kapanis_kar:.1f})\n\n"
            )
        except Exception:
            continue

    ort_kar = toplam_kar / len(gunluk_sinyaller) if gunluk_sinyaller else 0
    rapor += f"🎯 **Günlük Ortalama Max Potansiyel:** %{ort_kar:.1f}"
    send_telegram_msg(rapor)
    gunluk_sinyaller.clear()


# ==========================================
# 9. CANLI TARAMA VE PROGRAM BAŞLATICI
# ==========================================
def gorseldeki_birebir_test_mesajini_at():
    time.sleep(3)
    
    symbol = "TEST"
    last_price = 1.70
    tight_stop = 1.67
    vol_ratio = 2.9
    tp1, tp1_pct = 1.84, 8.5
    tp2, tp2_pct = 2.25, 32.4
    tv_url = f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"
    
    msg = (
        f"⚡ NASDAQ ALARMI: #{symbol}\n\n"
        f"📊 Sinyal Durumu: 🟢 Gerçek Kırılım 1 (Güçlü)\n"
        f"📝 Analiz: Direnç kırıldı, kırılım türü fotoğraftaki yapı ile eşleşiyor.\n\n"
        f"💵 Giriş / Kırılım: ${last_price:.2f}\n"
        f"🛡️ Stop (-%2.0): ${tight_stop:.2f}\n\n"
        f"📈 Hacim Gücü: {vol_ratio:.1f}x katı\n\n"
        f"🎯 1. Kademe Satış (+%{tp1_pct:.1f}): ${tp1:.2f}\n"
        f"🎯 2. Kademe Satış (+%{tp2_pct:.1f}): ${tp2:.2f}\n\n"
        f"🔗 [TradingView'de Grafiği Aç]({tv_url})"
    )
    send_telegram_side_photo(IMAGE_URLS["GERCEK_1"], msg)

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
    send_telegram_msg("🚀 **Nasdaq Scanner Aktif!**")
    threading.Thread(target=gorseldeki_birebir_test_mesajini_at, daemon=True).start()
    
    while True:
        canli_kesintisiz_tarama()

if __name__ == '__main__':
    threading.Thread(target=haber_tarama_loop, daemon=True).start()
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    run_flask()
