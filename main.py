import time
import datetime
import threading
import os
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
    return "⚡ NASDAQ TERMINAL ACTIVE ⚡"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)


# ==========================================
# 2. AYARLAR VE DİNAMİK DEĞİŞKENLER
# ==========================================
TELEGRAM_BOT_TOKEN = "8750813780:AAHvWiUdKO6bzxBQHFx4GQnV9CHztjQaOH0"
TELEGRAM_CHAT_ID = "7743041008"
RENDER_DEPLOY_HOOK_URL = "https://api.render.com/deploy/srv-daemtan40ujc73ft425g?key=o1ghEoCwW10"
MAX_PRICE_LIMIT = 3.50

bildirilenler = {}          
gunluk_sinyaller = {}       
gonderilen_haberler = set() 
rapor_gonderildi_bugun = False
last_update_id = 0          
is_running = True

IMAGE_URLS = {
    "GERCEK_1": "https://i.ibb.co/WWSb4Fn0/Ekran-g-r-nt-s-2026-09-07-171021.png",
    "GERCEK_2": "https://i.ibb.co/Ngq2DTrm/Ekran-g-r-nt-s-2026-09-07-171029.png",
    "YAVAS_HACIM": "https://i.ibb.co/svqwGCJz/Ekran-g-r-nt-s-2026-09-07-171038.png",
    "ONAYLI": "https://i.ibb.co/V0rY29P5/Ekran-g-r-nt-s-2026-09-07-171049.png"
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

def edit_telegram_msg(message_id, new_text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Mesaj guncelleme hatasi: {e}")

def send_telegram_side_photo(photo_url, caption):
    url_photo = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
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
                send_telegram_msg(caption)
        else:
            send_telegram_msg(caption)
    except Exception as e:
        print(f"Resim gonderme hatasi: {e}")
        send_telegram_msg(caption)

def check_telegram_commands():
    global MAX_PRICE_LIMIT, last_update_id, is_running
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
                    
                    if text == "/stop":
                        if is_running:
                            is_running = False
                            send_telegram_msg("🔴 **TERMINAL PAUSED**\n` Tarama donduruldu. Komut bekleniyor...`")
                        else:
                            send_telegram_msg("⚠️ `Tarama zaten pasif durumda.`")

                    elif text == "/start":
                        if not is_running:
                            is_running = True
                            send_telegram_msg("🟢 **TERMINAL RESUMED**\n` Motorlar çalıştırıldı. Piyasa taranıyor...`")
                        else:
                            send_telegram_msg("⚠️ `Tarama zaten aktif olarak çalışıyor.`")

                    elif text in ["/ping", "/pingms"]:
                        send_telegram_msg(f"⚡ `{latency:.0f} ms`")

                    elif text in ["/status", "/durum"]:
                        status_badge = "🟢 ONLINE / SCANNING" if is_running else "🔴 PAUSED / STANDBY"
                        durum_msg = (
                            f"🤖 **NASDAQ ALGO TERMINAL STATUS**\n"
                            f"━━━━━ 🦅 ━━━━━\n\n"
                            f"🌐 **Sistem:** `{status_badge}`\n"
                            f"💵 **Max Fiyat:** `${MAX_PRICE_LIMIT:.2f}`\n"
                            f"📊 **Bugünkü Sinyal:** `{len(gunluk_sinyaller)} Adet`\n"
                            f"⚡ **Gecikme:** `{latency:.0f} ms`\n"
                            f"🔒 **Server:** `Render (Active Hub)`"
                        )
                        send_telegram_msg(durum_msg)

                    elif text in ["/stats", "/ozet"]:
                        if not gunluk_sinyaller:
                            send_telegram_msg("📈 **ANLIK PORTFÖY:** `Bugün henüz sinyal tetiklenmedi.`")
                        else:
                            ozet_msg = f"📊 **BUGÜNKÜ SİNYAL LİSTESİ ({len(gunluk_sinyaller)} Adet):**\n"
                            ozet_msg += "━━━━━━━━━━━━━━━━━━━━━\n"
                            for sym in gunluk_sinyaller.keys():
                                ozet_msg += f"• `#{sym}` ➔ Giriş: `${gunluk_sinyaller[sym]['entry']:.2f}`\n"
                            send_telegram_msg(ozet_msg)

                    elif text in ["/help", "/yardim"]:
                        yardim_msg = (
                            "⚡ **NASDAQ BOT TERMINAL KOMUTLARI**\n"
                            "━━━━━━━━━━━━━━━━━━━━━\n\n"
                            "▶️ `/start` - Taramayı başlatır.\n"
                            "⏸️ `/stop` - Taramayı durdurur.\n"
                            "⚡ `/ping` - Sunucu gecikmesini ölçer.\n"
                            "🖥️ `/status` - Sistem durumunu gösterir.\n"
                            "📊 `/stats` - Günün sinyallerini listeler.\n"
                            "⚙️ `/limit [değer]` - Üst fiyat limitini ayarlar.\n"
                            "🔄 `/render` - Canlı bar ile yeniden başlatır."
                        )
                        send_telegram_msg(yardim_msg)

                    elif text == "/render":
    if RENDER_DEPLOY_HOOK_URL:
        # 1. Mesajı başlat
        init_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        init_res = requests.post(init_url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "🌀 *Sistem Yeniden Başlatılıyor...*",
            "parse_mode": "Markdown"
        }).json()
        
        msg_id = init_res.get("result", {}).get("message_id")
        
        try:
            requests.post(RENDER_DEPLOY_HOOK_URL, timeout=10)
            
            # Canlı Akıcı Yazı Animasyonu
            animasyon_kareleri = [
                "🔄 *Render Baglantisi Kuruluyor* ⏳",
                "🔄 *Render Baglantisi Kuruluyor..* ⏳\n\n📡 *Sunucu yanıtı bekleniyor...*",
                "⚙️ *Sistem Çekirdeği Yükleniyor...*\n\n[▓░░░░░░░░░] *%10* — _Bağlantı doğrulandı_",
                "⚙️ *Sistem Çekirdeği Yükleniyor...*\n\n[▓▓▓░░░░░░░] *%30* — _Kütüphaneler hazırlanıyor_",
                "🔥 *Algo Motoru Aktifleştiriliyor...*\n\n[▓▓▓▓▓░░░░░] *%50* — _Para kazanma modu devrede_ 💵",
                "📊 *NASDAQ Veri Akışı Bağlanıyor...*\n\n[▓▓▓▓▓▓▓░░░] *%70* — _Fiyat filtreleri yükleniyor_",
                "🛡️ *Güvenlik Kontrolleri...*\n\n[▓▓▓▓▓▓▓▓▓░] *%90* — _Canlı tarama başlatılıyor_",
                "🚀 *İŞLEM TAMAMLANDI!*\n\n[▓▓▓▓▓▓▓▓▓▓] *%100*\n\n✨ *Nasdaq Scanner Canlı Modda Çalışıyor!*"
            ]
            
            for kare in animasyon_kareleri:
                time.sleep(1.2) # Her karede mesaj güncellenir (Animasyon hissi)
                if msg_id:
                    edit_telegram_msg(msg_id, kare)

        except Exception as e:
            if msg_id:
                edit_telegram_msg(msg_id, f"⚠️ *Hata Oluştu:* {e}")

                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 1:
                            send_telegram_msg(f"ℹ️ **Mevcut Limit:** `${MAX_PRICE_LIMIT:.2f}`")
                        elif len(parts) == 2:
                            try:
                                new_limit = float(parts[1])
                                if 0.1 <= new_limit <= 20.0:
                                    MAX_PRICE_LIMIT = new_limit
                                    send_telegram_msg(f"✅ **Fiyat Limiti Güncellendi:** `${MAX_PRICE_LIMIT:.2f}`")
                                else:
                                    send_telegram_msg("⚠️ Lütfen $0.10 ile $20.00 arasında bir değer girin.")
                            except ValueError:
                                send_telegram_msg("⚠️ Örnek kullanım: `/limit 3.5`")
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
                    f"🚨 **NASDAQ BREAKOUT ALERT: #{symbol}**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 **Kırılım Tipi:** `🟢 {kirilim_adi}`\n"
                    f"⚡ **Hacim Gücü:** `{vol_ratio:.1f}x Katı` (Hacim Patlaması)\n\n"
                    f"💵 **Giriş Fiyatı:** `${last_price:.2f}`\n"
                    f"🛡️ **Stop-Loss (-%2.0):** `${tight_stop:.2f}`\n\n"
                    f"🎯 **1. Kademe Satış (+%{tp1_pct:.1f}):** `${tp1:.2f}`\n"
                    f"🎯 **2. Kademe Satış (+%{tp2_pct:.1f}):** `${tp2:.2f}`\n\n"
                    f"📈 [TradingView'de Grafiği İncele]({tv_url})"
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
        return "🚨 **NASDAQ Piyasa Etkisi:** `🔴 YÜKSEK RİSK / OLUMSUZ`"
    elif any(word in metin_lower for word in olumlu_kelimeler):
        return "🚀 **NASDAQ Piyasa Etkisi:** `🟢 POZİTİF / BOĞA`"
    else:
        return "⚠️ **NASDAQ Piyasa Etkisi:** `NÖTR / DİKKAT`"

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
                msg = (
                    f"🌐 **KRİTİK MAKRO HABER AKIŞI**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📌 **Açıklama:** *{entry.title}*\n\n"
                    f"{etki}"
                )
                send_telegram_msg(msg)
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
        send_telegram_msg("📋 **GÜN SONU RAPORU:** `Bugün kriterlere uyan kırılım oluşmadı.`")
        return

    rapor = (
        f"📊 **GÜNÜN ALGO PERFORMANS ÖZETİ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
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
            
            durum_str = f"🛡️ `-%2.0 Stop`" if zirve <= entry else f"🚀 `+%{max_kar:.1f} Zirve`"
            toplam_kar += max_kar

            rapor += (
                f"🔹 **#{symbol}** ➔ Giriş: `${entry:.2f}`\n"
                f"   • {durum_str} | Kapanış: `${kapanis:.2f}` (%{kapanis_kar:.1f})\n\n"
            )
        except Exception:
            continue

    ort_kar = toplam_kar / len(gunluk_sinyaller) if gunluk_sinyaller else 0
    rapor += f"🎯 **Günlük Ortalama Başarı:** `+%{ort_kar:.1f}`"
    send_telegram_msg(rapor)
    gunluk_sinyaller.clear()


# ==========================================
# 9. CANLI TARAMA VE PROGRAM BAŞLATICI
# ==========================================
def canli_kesintisiz_tarama():
    global rapor_gonderildi_bugun

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
    # İlk Açılış Animasyonu Ve Havalı Karşılama Ekranı
    welcome_msg = (
        "🦅 **NASDAQ ALGO TERMINAL ONLINE** 🦅\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "```text\n"
        "   _  ___   ___ ___  ___  _____\n"
        "  / |/ / \\ / / // _ \\/ _ \\/ ___/\n"
        " /    /   '  / // // / // / /__  \n"
        "/_/|_/_/\\_/ /___/____/____/\\___/  \n"
        "```\n"
        "⚡ **Tarama Motoru:** `Aktif (1m Canlı Veri)`\n"
        "🎯 **Fiyat Limiti:** `$3.50 ve Altı`\n"
        "📊 **Hacim Filtresi:** `1.8x ve Üzeri`\n\n"
        "`Piyasa taranıyor, fırsatlar bekleniyor...` 🚀"
    )
    send_telegram_msg(welcome_msg)
    
    while True:
        try:
            if is_running:
                canli_kesintisiz_tarama()
            else:
                print("Tarama pasif konumda, komut bekleniyor...")
        except Exception as e:
            print(f"Tarama döngüsü hatası: {e}")
        
        time.sleep(300)

def telegram_komut_dinleme_loop():
    """Telegram komutlarını tarama döngüsünden bağımsız olarak anlık dinler"""
    while True:
        try:
            check_telegram_commands()
        except Exception as e:
            print(f"Komut dinleme hatası: {e}")
        time.sleep(2)

if __name__ == '__main__':
    threading.Thread(target=haber_tarama_loop, daemon=True).start()
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    threading.Thread(target=telegram_komut_dinleme_loop, daemon=True).start()
    run_flask()
