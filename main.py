import time
import datetime
import threading
import os
import requests
import feedparser
from flask import Flask

# ==========================================
# 1. RENDER UYANIK TUTMA (FLASK SUNUCUSU)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ FINNHUB REALTIME TERMINAL ACTIVE ⚡"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)


# ==========================================
# 2. AYARLAR VE DİNAMİK DEĞİŞKENLER
# ==========================================
TELEGRAM_BOT_TOKEN = "8750813780:AAHvWiUdKO6bzxBQHFx4GQnV9CHztjQaOH0"
TELEGRAM_CHAT_ID = "7743041008"
RENDER_DEPLOY_HOOK_URL = "https://api.render.com/deploy/srv-daemtan40ujc73ft425g?key=o1ghEoCwW10"

# FINNHUB API KEY'İNİZİ BURAYA YAPIŞTIRIN:
FINNHUB_API_KEY = "BURAYA_FINNHUB_API_KEY_YAZIN"

MAX_PRICE_LIMIT = 3.00

# Tekrarlı mesajı engellemek için son bildiri fiyatı tutulur
son_bildirilen_fiyat = {}    
gunluk_sinyaller = {}       
gonderilen_haberler = set() 
rapor_gonderildi_bugun = False
last_update_id = 0          
is_running = True
last_heartbeat_time = 0


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
                            send_telegram_msg("🔴 *BOT DURDURULDU*\n_Tarama donduruldu._")
                        else:
                            send_telegram_msg("⚠️ _Tarama zaten pasif durumda._")

                    elif text == "/start":
                        if not is_running:
                            is_running = True
                            send_telegram_msg("🟢 *BOT DEVREDE*\n_Piyasa taranıyor..._")
                        else:
                            send_telegram_msg("⚠️ _Tarama zaten aktif olarak çalışıyor._")

                    elif text in ["/ping", "/pingms"]:
                        send_telegram_msg(f"⚡ *Gecikme Süresi:* `{latency:.0f} ms`")

                    elif text in ["/status", "/durum"]:
                        status_badge = "🟢 AKTİF" if is_running else "🔴 PASİF"
                        durum_msg = (
                            f"🤖 *NASDAQ SCANNER DURUMU*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"🌐 *Sistem:* {status_badge}\n"
                            f"💵 *Max Fiyat:* `${MAX_PRICE_LIMIT:.2f}`\n"
                            f"📊 *Günlük Sinyal:* `{len(gunluk_sinyaller)} Adet`\n"
                            f"⚡ *Gecikme:* `{latency:.0f} ms`\n"
                            f"🚀 *Veri Kaynağı:* `Finnhub Real-Time API`"
                        )
                        send_telegram_msg(durum_msg)

                    elif text in ["/stats", "/ozet"]:
                        if not gunluk_sinyaller:
                            send_telegram_msg("📈 *ANLIK PORTFÖY:* _Bugün henüz sinyal tetiklenmedi._")
                        else:
                            ozet_msg = f"📊 *BUGÜNKÜ SİNYAL LİSTESİ ({len(gunluk_sinyaller)} Adet):*\n"
                            ozet_msg += "━━━━━━━━━━━━━━━━━━━━━\n"
                            for sym in gunluk_sinyaller.keys():
                                ozet_msg += f"• *#{sym}* ➔ Giriş: `${gunluk_sinyaller[sym]['entry']:.2f}`\n"
                            send_telegram_msg(ozet_msg)

                    elif text in ["/help", "/yardim"]:
                        yardim_msg = (
                            "⚡ *KOMUTLAR*\n"
                            "━━━━━━━━━━━━━━━━━━━━━\n\n"
                            "▶️ `/start` - Scannerı başlatır.\n"
                            "⏸️ `/stop` - Scannerı durdurur.\n"
                            "⚡ `/ping` - Sunucu gecikmesini ölçer.\n"
                            "🖥️ `/status` - Sistem durumunu gösterir.\n"
                            "📊 `/stats` - Günün sinyallerini listeler.\n"
                            "⚙️ `/limit [değer]` - Üst fiyat limitini ayarlar.\n"
                            "🔄 `/render` - Sunucuyu yeniden başlatır."
                        )
                        send_telegram_msg(yardim_msg)

                    elif text == "/render":
                        if RENDER_DEPLOY_HOOK_URL:
                            init_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            init_res = requests.post(init_url, json={
                                "chat_id": TELEGRAM_CHAT_ID,
                                "text": "🌀 *Sisteme Render Atılıyor...*",
                                "parse_mode": "Markdown"
                            }).json()
                            
                            msg_id = init_res.get("result", {}).get("message_id")
                            
                            try:
                                requests.post(RENDER_DEPLOY_HOOK_URL, timeout=10)
                                time.sleep(2)
                                if msg_id:
                                    edit_telegram_msg(msg_id, "🚀 *Sistem Yeniden Başlatıldı!*")
                            except Exception as e:
                                if msg_id:
                                    edit_telegram_msg(msg_id, f"⚠️ *Deploy Hatası:* {e}")

                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 1:
                            send_telegram_msg(f"ℹ️ *Mevcut Limit:* `${MAX_PRICE_LIMIT:.2f}`")
                        elif len(parts) == 2:
                            try:
                                new_limit = float(parts[1])
                                if 0.1 <= new_limit <= 20.0:
                                    MAX_PRICE_LIMIT = new_limit
                                    send_telegram_msg(f"✅ *Fiyat Limiti Güncellendi:* `${MAX_PRICE_LIMIT:.2f}`")
                                else:
                                    send_telegram_msg("⚠️ Lütfen $0.10 ile $20.00 arasında bir değer girin.")
                            except ValueError:
                                send_telegram_msg("⚠️ Örnek kullanım: `/limit 3.5`")
    except Exception:
        pass


# ==========================================
# 4. FINNHUB ANLIK CANLI SİNYAL TARAMA
# ==========================================
def get_quote_finnhub(symbol):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
        res = requests.get(url, timeout=5).json()
        
        if "c" in res and res["c"] > 0:
            return {
                "current": res["c"],
                "high": res["h"],
                "low": res["l"],
                "open": res["o"],
                "prev_close": res["pc"]
            }
    except Exception:
        pass
    return None

def process_symbol(symbol):
    data = get_quote_finnhub(symbol)
    if not data:
        return

    last_price = data["current"]
    prev_close = data["prev_close"]
    high_price = data["high"]

    # Fiyat sınır kontrolleri
    if last_price >= MAX_PRICE_LIMIT or last_price <= 0.05 or prev_close <= 0:
        return

    pct_change = ((last_price - prev_close) / prev_close) * 100

    # Temel kriter: En az %2 artış
    if pct_change >= 2.0:
        # TEKRAR ENGELLEME FİLTRESİ:
        # Hisse daha önce hiç atılmadıysa VEYA fiyatı son atılan seviyenin en az %3 yukarısına kırıldıysa at.
        if symbol not in son_bildirilen_fiyat or last_price >= (son_bildirilen_fiyat[symbol] * 1.03):
            tight_stop = last_price * 0.95
            tp1 = last_price * 1.10
            tp2 = last_price * 1.25
            
            tv_url = f"https://www.tradingview.com/chart/?symbol=NASDAQ%3A{symbol}"

            msg = (
                f"🚨 *NASDAQ REAL-TIME SİNYAL: #{symbol}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💵 *Giriş Fiyatı:* `${last_price:.2f}`\n"
                f"🎯 *Anlık Tepe:* `${high_price:.2f}`\n"
                f"📊 *Günlük Artış:* `%{pct_change:.2f}`\n\n"
                f"🛡️ *Stop-Loss:* `${tight_stop:.2f}`\n"
                f"🎯 *1. Hedef:* `${tp1:.2f}`\n"
                f"🎯 *2. Hedef:* `${tp2:.2f}`\n\n"
                f"📈 [TradingView Full Chart]({tv_url})"
            )
            
            send_telegram_msg(msg)
            
            # Son bildirilen fiyatı güncelle
            son_bildirilen_fiyat[symbol] = last_price
            
            if symbol not in gunluk_sinyaller:
                gunluk_sinyaller[symbol] = {'entry': last_price}


# ==========================================
# 5. TRUMP VE HABER MODÜLÜ
# ==========================================
def kritik_piyasa_etkisi_analiz_et(metin):
    metin_lower = metin.lower()
    olumsuz_kelimeler = ["war", "strike", "attack", "sanction", "tariff", "tariffs", "threat", "china", "russia", "ban"]

    if any(word in metin_lower for word in olumsuz_kelimeler):
        return "🚨 *Haber Etkisi:* `🔴 NEGATİF`"
    else:
        return "🚀 *Haber Etkisi:* `🟢 POZİTİF`"

def trump_ve_piyasa_haberleri_kontrol_et():
    global gonderilen_haberler
    rss_url = "https://news.google.com/rss/search?q=Trump+(war+OR+tariff+OR+sanction+OR+attack+OR+China+OR+strike)&hl=en-US&gl=US&ceid=US:en"
    kritik_kelimeler = ["war", "tariff", "tariffs", "sanction", "attack", "strike", "china", "russia", "military", "missile", "threat", "ban"]
    
    try:
        feed = feedparser.parse(rss_url)
        
        for entry in feed.entries[:5]:
            haber_id = entry.title
            baslik_lower = entry.title.lower()
            
            if haber_id in gonderilen_haberler:
                continue
                
            if any(word in baslik_lower for word in kritik_kelimeler):
                etki = kritik_piyasa_etkisi_analiz_et(entry.title)
                msg = (
                    f"🌐 *KRİTİK HABER BÜLTENİ*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📌 *Açıklama:* _{entry.title}_\n\n"
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
# 6. GÜN SONU PERFORMANS RAPORU
# ==========================================
def gun_sonu_raporu_gonder():
    global gunluk_sinyaller, son_bildirilen_fiyat
    if not gunluk_sinyaller:
        send_telegram_msg("PARA KAZANMA SANATI\n📊 **GÜNÜN İŞLEMLERİ** 📊\n\n`Bugün henüz sinyal oluşmadı.`")
        return

    rapor = "PARA KAZANMA SANATI\n📊 **GÜNÜN İŞLEMLERİ** 📊\n"
    toplam_kar = 0
    basarili_sayisi = 0

    for symbol, data in gunluk_sinyaller.items():
        try:
            q = get_quote_finnhub(symbol)
            entry = data['entry']
            zirve = q["high"] if q else entry
            
            kar_pct = ((zirve - entry) / entry) * 100
            toplam_kar += kar_pct
            basarili_sayisi += 1

            emoji = "🚀🔥" if kar_pct >= 30 else ("🔥" if kar_pct >= 20 else "💰")
            rapor += f"🟢 `{symbol.ljust(5)}` ➔ `{entry:.2f}` ➡️ `{zirve:.2f} gördü` | `%{kar_pct:.2f} kâr` {emoji}\n"
        except Exception:
            continue

    if basarili_sayisi > 0:
        ort_kar = toplam_kar / basarili_sayisi
        rapor += f"\n📈 **Ortalama Kâr:** `%{ort_kar:.2f}`"
        rapor += f"\n📈 **Toplam Getiri:** `%{toplam_kar:.2f}`"
    
    send_telegram_msg(rapor)
    
    # Gece yarısı hafızayı temizle
    gunluk_sinyaller.clear()
    son_bildirilen_fiyat.clear()


# ==========================================
# 7. CANLI TARAMA VE PROGRAM BAŞLATICI
# ==========================================
WATCHLIST = ["ARBE", "CDTG", "BJDX", "WDH", "BBAI", "SOUN", "GNS", "NVOS", "TNSL", "MULN", "KOSS", "VISL", "VERB", "SNOA", "EBON"]

def canli_kesintisiz_tarama():
    global rapor_gonderildi_bugun, last_heartbeat_time

    now_ts = time.time()
    if now_ts - last_heartbeat_time >= 900:
        su_an = (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).strftime("%H:%M")
        send_telegram_msg(f"🔎 *Piyasa taranıyor...* `[{su_an}]`")
        last_heartbeat_time = now_ts

    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    if now.hour == 23 and now.minute == 0:
        if not rapor_gonderildi_bugun:
            gun_sonu_raporu_gonder()
            rapor_gonderildi_bugun = True
    elif now.hour == 0:
        rapor_gonderildi_bugun = False

    for sym in WATCHLIST:
        process_symbol(sym)
        time.sleep(1)

def start_scanner_loop():
    welcome_msg = (
        "⚡ *NASDAQ SCANNER REALTIME ONLINE* ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎯 *Fiyat Limiti:* `$3.00`\n"
        "🚀 *Altyapı:* `Finnhub Real-Time API`\n"
        "_Piyasa taranıyor..._ 🚀"
    )
    send_telegram_msg(welcome_msg)
    
    while True:
        try:
            if is_running:
                canli_kesintisiz_tarama()
            else:
                print("Tarama pasif...")
        except Exception as e:
            print(f"Tarama döngüsü hatası: {e}")
        
        time.sleep(5)

def telegram_komut_dinleme_loop():
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
