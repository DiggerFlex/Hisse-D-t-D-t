import time
import datetime
import threading
import requests
import feedparser
import yfinance as yf
import pandas as pd
from dateutil import parser
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

# ==========================================
# 1. RENDER UYANIK TUTMA (FLASK SUNUCUSU)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "NASDAQ Scanner Active"

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
# 5. HİSSE BAZLI CANLI FİLTRELEME & ERKEN ALARM (KIRILIM ÖNCESİ)
# ==========================================
def process_symbol(symbol):
    """Fiyat henüz direnci kırmadan, dirence dayandığı ve hacim patladığı an alarm atar."""
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
        open_price = df['Open'].iloc[-1]
        
        # Son mum hariç günün en yüksek fiyatı (Direnç)
        resistance = df['High'][:-1].max()
        avg_volume = df['Volume'][:-1].mean()

        if avg_volume < 1000:
            return

        # --- KIRILIM ÖNCESİ (PRE-BREAKOUT) MANTIGI ---
        distance_to_resistance = (resistance - last_price) / resistance if resistance > 0 else 1.0
        vol_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0

        if last_price <= resistance and distance_to_resistance <= 0.015 and last_price > open_price:
            
            # Hacim gücüne göre sinyal derecelendirme
            if vol_ratio >= 3.0:
                risk_durumu = "🔥 Patlama Yakın"
                aciklama = "Fiyat dirence dayandı, devasa hacimle direnci zorluyor!"
            elif vol_ratio >= 2.0:
                risk_durumu = "🟡 Kırılım Hazırlığı"
                aciklama = "Direncin hemen altında, hacim artışı var."
            else:
                return  # Hacimsiz yaklaşmaları ele

            if symbol not in bildirilenler or (time.time() - bildirilenler[symbol]) > 60:
                # Stop seviyesi: İzin verilen en yüksek değer olan tam %2.0 kayıp sınırı
                tight_stop = last_price * 0.98   
                tp1 = resistance * 1.05          # Direnç kırıldıktan sonraki +%5
                tp2 = resistance * 1.10          # Direnç kırıldıktan sonraki +%10

                tv_url = f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"

                msg = (
                    f"⚡ **NASDAQ ERKEN ALARM: #{symbol}**\n\n"
                    f"📊 **Sinyal Durumu:** {risk_durumu}\n"
                    f"📝 **Analiz:** {aciklama}\n\n"
                    f"💵 **Anlık Fiyat:** ${last_price:.2f}\n"
                    f"🎯 **Test Edilen Direnç:** ${resistance:.2f}\n"
                    f"🛡️ **Stop (-%2.0 Maksimum):** ${tight_stop:.2f}\n"
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
# 6. TRUMP KRİZ VE ŞOK AÇIKLAMA MODÜLÜ (SAAT BAŞI FİLTRELİ)
# ==========================================
def kritik_piyasa_etkisi_analiz_et(metin):
    """Haber başlığını analiz eder ve sadece Türkçe net yön bilgisi döndürür."""
    metin_lower = metin.lower()
    
    olumlu_kelimeler = [
        "cut tariffs", "tax cut", "trade deal", "peace", "agreement", 
        "support", "boost", "surge", "deregulation", "growth"
    ]
    olumsuz_kelimeler = [
        "war", "strike", "attack", "sanction", "tariff", "tariffs", 
        "threat", "china", "russia", "ban", "military", "missile", "crisis"
    ]

    olumlu_puan = sum(1 for word in olumlu_kelimeler if word in metin_lower)
    olumsuz_puan = sum(1 for word in olumsuz_kelimeler if word in metin_lower)

    if olumsuz_puan > 0:
        return "🚨 **NASDAQ Etkisi: Olumsuz**"
    elif olumlu_puan > 0:
        return "🚀 **NASDAQ Etkisi: Olumlu**"
    else:
        return "⚠️ **NASDAQ Etkisi: Riskli**"

def trump_ve_piyasa_haberleri_kontrol_et():
    """Saat başlarında çalışır; sadece son 60 dakikadaki KRİTİK kriz haberlerini atar."""
    global gonderilen_haberler
    
    rss_url = "https://news.google.com/rss/search?q=Trump+(war+OR+tariff+OR+sanction+OR+attack+OR+China+OR+strike)&hl=en-US&gl=US&ceid=US:en"
    
    kritik_kelimeler = [
        "war", "tariff", "tariffs", "sanction", "attack", "strike", 
        "china", "russia", "military", "missile", "threat", "ban", "trade war"
    ]
    
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
                
                haber_mesaji = (
                    f"⚠️ **Trump Açıklama** ⚠️\n\n"
                    f"{etki}"
                )
                
                send_telegram_msg(haber_mesaji)
                gonderilen_haberler.add(haber_id)
                
    except Exception as e:
        print(f"Haber akisi hatasi: {e}")

def haber_tarama_loop():
    """Haber kontrolünü saat başlarında (her 3600 saniyede bir) çalıştırır."""
    while True:
        trump_ve_piyasa_haberleri_kontrol_et()
        time.sleep(3600)


# ==========================================
# 7. GÜN SONU PERFORMANS RAPORU
# ==========================================
def gun_sonu_raporu_gonder():
    global gunluk_sinyaller
    if not gunluk_sinyaller:
        send_telegram_msg("📊 **GÜN SONU RAPORU:** Bugün kriterlere uyan kırılmalar oluşmadı.")
        return

    rapor = "📊 **GÜNÜN MİDAS PERFORMANS ÖZETİ**\n\n"
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
# 8. CANLI TARAMA VE PROGRAM BAŞLATICI
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
    send_telegram_msg("🚀 **Nasdaq Haber Scanner Aktif!**")
    while True:
        canli_kesintisiz_tarama()

if __name__ == '__main__':
    # Haber takip sistemini saat başlarında çalışacak şekilde başlatır
    threading.Thread(target=haber_tarama_loop, daemon=True).start()
    
    # Canlı hisse tarama sistemini kesintisiz başlatır
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    
    # Render uyanık tutma sunucusu
    run_flask()
