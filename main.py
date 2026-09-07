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

MAX_PRICE_LIMIT = 3.50

bildirilenler = {}          
gunluk_sinyaller = {}       
gonderilen_haberler = set() 
rapor_gonderildi_bugun = False
last_update_id = 0          

# Kırılım tiplerine ait temsilî görsel URL'leri (Kendi resim URL'lerin ile değiştirebilirsin)
IMAGE_URLS = {
    "GERCEK_1": "https://i.imgur.com/8Q9Z3v1.png",      # Gerçek Kırılım (Güçlü Yeşil Mum)
    "GERCEK_2": "https://i.imgur.com/5X2A7y8.png",      # Gerçek Kırılım (Fitilli/Geri Çekilmeli)
    "YAVAS_HACIM": "https://i.imgur.com/2N1M9p0.png",   # Yavaş Hacimli Kırılım
    "ONAYLI": "https://i.imgur.com/9K4L7w2.png"        # Onaylı Kırılım (Retest)
}


# ==========================================
# 3. TELEGRAM İLETİŞİM FONKSİYONLARI (FOTOĞRAFLI)
# ==========================================
def send_telegram_msg(message):
    """Metin odaklı Telegram mesajı gönderir."""
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
        print(f"Telegram Baglanti Hatasi: {e}")

def send_telegram_photo(photo_url, caption):
    """Görsel ve altına açıklama metni ekleyerek Telegram mesajı gönderir."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload)
        if res.status_code != 200:
            # Görsel gönderilemezse yedek olarak sadece metin gönderir
            send_telegram_msg(caption)
    except Exception:
        send_telegram_msg(caption)

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
    """
    Görseldeki 4 senaryodan hangisinin gerçekleştiğini tespit eder.
    """
    prev_close = df['Close'].iloc[-2]
    prev_high = df['High'].iloc[-2]
    prev_low = df['Low'].iloc[-2]
    
    # 1. Onaylı Kırılım (Retest): Önceki mum direnci kırdı, şu anki mum dirence temas edip tepki veriyor
    if prev_high > resistance and last_price >= resistance:
        return "Onaylı Kırılım (Retest)", IMAGE_URLS["ONAYLI"]
        
    # 2. Yavaş Hacimli Kırılım: Hacim katı düşük (2.0x - 2.8x arası) ama yükseliş var
    elif 2.0 <= vol_ratio < 2.8:
        return "Yavaş Hacimli Kırılım", IMAGE_URLS["YAVAS_HACIM"]
        
    # 3. Gerçek Kırılım 2 (Fitilli/Aşağı İğneli Kırılım): Mum altında iğne bırakıp toparlamış
    elif vol_ratio >= 2.8 and (df['Open'].iloc[-1] < prev_low or df['Low'].iloc[-1] < prev_close):
        return "Gerçek Kırılım (İğneli)", IMAGE_URLS["GERCEK_2"]
        
    # 4. Gerçek Kırılım 1 (Doğrudan Güçlü Hacimli Kırılım)
    else:
        return "Gerçek Kırılım (Güçlü)", IMAGE_URLS["GERCEK_1"]


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
            
            if vol_ratio >= 2.0:
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
                    f"📝 Analiz: Kırılım yapısı tespit edildi, grafik eşleşti.\n\n"
                    f"💵 Giriş / Kırılım: ${last_price:.2f}\n"
                    f"🛡️ Stop (-%2.0): ${tight_stop:.2f}\n"
                    f"📈 Hacim Gücü: {vol_ratio:.1f}x katı\n\n"
                    f"🎯 1. Kademe Satış (+%{tp1_pct:.1f}): ${tp1:.2f}\n"
                    f"🎯 2. Kademe Satış (+%{tp2_pct:.1f}): ${tp2:.2f}\n\n"
                    f"🔥 MOTİVASYON: Obez olma !\n\n"
                    f"🔗 [TradingView'de Grafiği Aç]({tv_url})"
                )
                
                # Görsel ile birlikte mesaj gönderimi
                send_telegram_photo(img_url, msg)
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
    olumlu_kelimeler = ["cut tariffs", "tax cut", "trade deal", "peace", "agreement", "support", "boost", "surge", "deregulation", "growth"]
    olumsuz_kelimeler = ["war", "strike", "attack", "sanction", "tariff", "tariffs", "threat", "china", "russia", "ban", "military", "missile", "crisis"]

    olumlu_puan = sum(1 for word in olumlu_kelimeler if word in metin_lower)
    olumsuz_puan = sum(1 for word in olumsuz_kelimeler if word in metin_lower)

    if olumsuz_puan > 0:
        return "🚨 **NASDAQ Etkisi: Olumsuz**"
    elif olumlu_puan > 0:
        return "🚀 **NASDAQ Etkisi: Olumlu**"
    else:
        return "⚠️ **NASDAQ Etkisi: Riskli**"

def trump_ve_piyasa_haberleri_kontrol_et():
    global gonderilen_haberler
    rss_url = "https://news.google.com/rss/search?q=Trump+(war+OR+tariff+OR+sanction+OR+attack+OR+China+OR+strike)&hl=en-US&gl=US&ceid=US:en"
    kritik_kelimeler = ["war", "tariff", "tariffs", "sanction", "attack", "strike", "china", "russia", "military", "missile", "threat", "ban", "trade war"]
    
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
                    
                    zaman_farki_dakika = (now_utc - pub_time).total_seconds() / 60.0
                    if zaman_farki_dakika > 60:
                        gonderilen_haberler.add(haber_id)
                        continue
                except Exception:
                    pass

            if any(word in baslik_lower for word in kritik_kelimeler):
                etki = kritik_piyasa_etkisi_analiz_et(entry.title)
                haber_mesaji = f"⚠️ **Trump Açıklama** ⚠️\n\n{etki}"
                send_telegram_msg(haber_mesaji)
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
# 9. CANLI TARAMA VE PROGRAM BAŞLATICI (FOTOĞRAFLI TEST)
# ==========================================
def gorseldeki_birebir_test_mesajini_at():
    """Fotoğraflı mesaj sistemini test eder."""
    time.sleep(3)
    
    symbol = "CISO"
    last_price = 1.70
    tight_stop = 1.67
    vol_ratio = 2.9
    tp1, tp1_pct = 1.84, 8.5
    tp2, tp2_pct = 2.25, 32.4
    tv_url = f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"
    
    msg = (
        f"⚡ NASDAQ ALARMI: #{symbol}\n\n"
        f"📊 Sinyal Durumu: 🟢 Gerçek Kırılım (Güçlü)\n"
        f"📝 Analiz: Direnç kırıldı, kırılım türü fotoğraftaki yapı ile eşleşiyor.\n\n"
        f"💵 Giriş / Kırılım: ${last_price:.2f}\n"
        f"🛡️ Stop (-%2.0): ${tight_stop:.2f}\n"
        f"📈 Hacim Gücü: {vol_ratio:.1f}x katı\n\n"
        f"🎯 1. Kademe Satış (+%{tp1_pct:.1f}): ${tp1:.2f}\n"
        f"🎯 2. Kademe Satış (+%{tp2_pct:.1f}): ${tp2:.2f}\n\n"
        f"🔥 MOTİVASYON: Obez olma !\n\n"
        f"🔗 [TradingView'de Grafiği Aç]({tv_url})"
    )
    send_telegram_photo(IMAGE_URLS["GERCEK_1"], msg)

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
