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
from zoneinfo import ZoneInfo

# ==========================================
# 1. RENDER UYANIK TUTMA (FLASK SUNUCUSU)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ NASDAQ SCANNER ACTIVE ⚡"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)


# ==========================================
# 2. AYARLAR VE DİNAMİK DEĞİŞKENLER
# ==========================================
TELEGRAM_BOT_TOKEN = "8750813780:AAHKpVFsxqT6BgYbISMZhiAp-ryzNsZ8IZY"
TELEGRAM_CHAT_ID = "7743041008"
RENDER_DEPLOY_HOOK_URL = "https://api.render.com/deploy/srv-daemtan40ujc73ft425g?key=o1ghEoCwW10"
MAX_PRICE_LIMIT = 3.00

bildirilenler = set()       
gunluk_sinyaller = {}       # { 'SYMBOL': {'entry': price, 'tp1': val, 'tp2': val} }
gonderilen_haberler = set() 

rapor_gonderildi_bugun = False
acilis_bildirildi_bugun = False
son_gun_str = ""

last_update_id = 0         
is_running = True

lock = threading.Lock()


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
        print(f"Telegram Hatasi: {e}")

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
                            send_telegram_msg("🔴 *BOT DURDURULDU*")
                        else:
                            send_telegram_msg("⚠️ _Tarama zaten pasif._")

                    elif text == "/start":
                        if not is_running:
                            is_running = True
                            send_telegram_msg("🟢 *BOT DEVREDE*\n_Tüm NASDAQ taranıyor..._")
                        else:
                            send_telegram_msg("⚠️ _Tarama zaten aktif._")

                    elif text in ["/ping", "/pingms"]:
                        send_telegram_msg(f"⚡ *Gecikme:* `{latency:.0f} ms`")

                    elif text == "/rapor":
                        send_telegram_msg("⏳ *Gün sonu raporu oluşturuluyor...*")
                        gun_sonu_raporu_gonder()

                    elif text in ["/status", "/durum"]:
                        status_badge = "🟢 AKTİF" if is_running else "🔴 PASİF"
                        durum_msg = (
                            f"🤖 *TERMINAL DURUMU*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"🌐 *Sistem:* {status_badge}\n"
                            f"💵 *Max Fiyat:* `${MAX_PRICE_LIMIT:.2f}`\n"
                            f"📊 *Bugünkü Sinyal:* `{len(gunluk_sinyaller)}`\n"
                            f"⚡ *Gecikme:* `{latency:.0f} ms`"
                        )
                        send_telegram_msg(durum_msg)

                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 2:
                            try:
                                new_limit = float(parts[1])
                                MAX_PRICE_LIMIT = new_limit
                                send_telegram_msg(f"✅ *Limit Güncellendi:* `${MAX_PRICE_LIMIT:.2f}`")
                            except ValueError:
                                send_telegram_msg("⚠️ Örnek: `/limit 3.5`")
    except Exception:
        pass


# ==========================================
# 4. TÜM NASDAQ LİSTESİNİ ALMA
# ==========================================
def get_penny_stocks():
    while True:
        try:
            url = "https://old.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"
            df = pd.read_csv(url, sep="|", timeout=15)
            df = df[(df['NASDAQ Symbol'].notnull()) & (df['ETF'] == 'N') & (df['Test Issue'] == 'N')]
            symbols = df['NASDAQ Symbol'].str.strip().tolist()
            clean_symbols = [s.replace('.', '-') for s in symbols if isinstance(s, str) and len(s) <= 5]
            
            if len(clean_symbols) > 100:
                return clean_symbols
        except Exception as e:
            print(f"Borsa listesi alinamadi, tekrar deneniyor... {e}")
            time.sleep(5)


# ==========================================
# 5. HEDEF VE ANALİZ MOTORU
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

        tp1 = max(last_price * 1.05, last_price + (1.5 * atr))
        tp1_pct = ((tp1 - last_price) / last_price) * 100

        tp2 = max(last_price * 1.15, last_price + (3.5 * atr))
        tp2_pct = ((tp2 - last_price) / last_price) * 100

        return tp1, tp1_pct, tp2, tp2_pct
    except Exception:
        return last_price * 1.07, 7.0, last_price * 1.25, 25.0


# ==========================================
# 6. SİNYAL GÖNDERİMİ
# ==========================================
def trigger_signal(symbol, df, last_price, vol_ratio):
    with lock:
        if symbol in bildirilenler:
            return
        bildirilenler.add(symbol)

    tight_stop = last_price * 0.98    
    tp1, tp1_pct, tp2, tp2_pct = calculate_dynamic_targets(df, last_price)
    tv_url = f"https://www.tradingview.com/chart/?symbol={symbol}"

    msg = (
        f"🚨 *SİNYAL: #{symbol}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 *Durum:* `Hacim Kırılımı`\n"
        f"⚡ *Hacim:* `{vol_ratio:.1f}x`\n\n"
        f"💵 *Giriş:* `${last_price:.2f}`\n"
        f"🛡️ *Stop-Loss:* `${tight_stop:.2f}`\n\n"
        f"🎯 *1. Hedef (+%{tp1_pct:.1f}):* `${tp1:.2f}`\n"
        f"🎯 *2. Hedef (+%{tp2_pct:.1f}):* `${tp2:.2f}`\n\n"
        f"📈 [Grafik]({tv_url})"
    )
    
    send_telegram_msg(msg)
    
    if symbol not in gunluk_sinyaller:
        gunluk_sinyaller[symbol] = {
            'entry': last_price,
            'tp1': tp1,
            'tp2': tp2
        }


# ==========================================
# 7. %100 EKSİKSİZ TOPLU (BATCH) TARAMA
# ==========================================
def tum_nasdaq_tarama_kesin():
    symbols = get_penny_stocks()
    if not symbols:
        return

    chunk_size = 100
    for i in range(0, len(symbols), chunk_size):
        if not is_running:
            break
            
        chunk = symbols[i:i + chunk_size]
        try:
            # 100 hisseyi tek seferde indirir (IP ban yemez, hızı 50 katına çıkarır)
            data = yf.download(
                tickers=" ".join(chunk), 
                period="1d", 
                interval="1m", 
                group_by='ticker', 
                progress=False, 
                prepost=True
            )
            
            for symbol in chunk:
                try:
                    df = data[symbol].dropna() if len(chunk) > 1 else data.dropna()
                    if df.empty or len(df) < 2:
                        continue
                    
                    last_price = df['Close'].iloc[-1]
                    
                    if last_price > MAX_PRICE_LIMIT or last_price <= 0.05:
                        continue
                        
                    last_volume = df['Volume'].iloc[-1]
                    price_spread = df['High'].iloc[-1] - df['Low'].iloc[-1]

                    # ⚠️ MANİPÜLASYON / SPOOFING TESPİTİ
                    if price_spread > 0 and last_volume <= 1:
                        send_telegram_msg(
                            f"⚠️ *MANİPÜLASYON ŞÜPHESİ: #{symbol}*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• Son 30sn/1dk içinde emir defterinde hareketlilik var ama *sadece {int(last_volume)} gerçek işlem* gerçekleşti.\n"
                            f"• Çok fazla emir veriliyor/iptal ediliyor, gerçek alım-satım neredeyse yok.\n"
                            f"_(bu bir tespit sinyalidir, kesin kanıt değildir)._"
                        )

                    # 🚨 BREAKOUT / HACİM SINYALİ TESPİTİ
                    if symbol not in bildirilenler:
                        resistance = df['High'][:-1].max()
                        avg_vol = df['Volume'][:-1].mean()
                        vol_ratio = last_volume / avg_vol if avg_vol > 0 else 1.0
                        
                        if (last_price >= resistance * 0.985) or (vol_ratio >= 1.5):
                            trigger_signal(symbol, df, last_price, vol_ratio)

                except Exception:
                    continue
        except Exception as e:
            print(f"Tarama hatasi: {e}")
            
        time.sleep(1) # Yahoo IP koruması için kısa bekleme


# ==========================================
# 8. BİREBİR GÖRSEL FORMATINDA GÜN SONU RAPORU
# ==========================================
def gun_sonu_raporu_gonder():
    global gunluk_sinyaller
    if not gunluk_sinyaller:
        send_telegram_msg("PARA KAZANMA SANATI\n📊 *GÜNÜN İŞLEMLERİ* 📊\n\n`Bugün sinyal oluşmadı.`")
        return

    rapor = "PARA KAZANMA SANATI\n📊 *GÜNÜN İŞLEMLERİ* 📊\n"
    toplam_kar = 0
    basarili_sayisi = 0

    for symbol, data in gunluk_sinyaller.items():
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="1m", prepost=True)
            
            entry = data['entry']
            tp1 = data.get('tp1', entry * 1.05)
            tp2 = data.get('tp2', entry * 1.15)
            
            zirve = df['High'].max() if not df.empty else entry
            
            # Kademe kontrolü
            if zirve >= tp2:
                kademe_str = "(TÜM kademeler tamam, gün içi)"
                hedef_fiyat = tp2
            elif zirve >= tp1:
                kademe_str = "(kademe tamam)"
                hedef_fiyat = tp1
            else:
                kademe_str = "(takipte)"
                hedef_fiyat = zirve

            kar_pct = ((zirve - entry) / entry) * 100
            
            # Emoji Seçimi
            if kar_pct >= 50:
                emoji = "🚀🚀🚀"
            elif kar_pct >= 20:
                emoji = "🔥"
            else:
                emoji = "💰"

            rapor += f"🟢 *{symbol}* ➔ `{entry:.2f}` ➡️ `{hedef_fiyat:.2f}` {kademe_str} | `%{kar_pct:.2f}` kâr {emoji}\n"
            
            # Görseldeki alt bilgilendirme detayı
            if zirve > tp2 and kar_pct > 15:
                rapor += f"└ Gün içi {zirve:.2f}'e yükseldi ➔ anlık %{kar_pct:.2f} 🔥\n"

            toplam_kar += kar_pct
            basarili_sayisi += 1

        except Exception as e:
            print(f"Rapor hatasi ({symbol}): {e}")
            continue

    if basarili_sayisi > 0:
        ort_kar = toplam_kar / basarili_sayisi
        rapor += f"\n📈 *Ortalama Kâr: %{ort_kar:.2f}*"
    
    send_telegram_msg(rapor)
    gunluk_sinyaller.clear()
    
    with lock:
        bildirilenler.clear()


# ==========================================
# 9. PİYASA SAATİ VE ANA DÖNGÜLER
# ==========================================
def piyasa_zaman_kontrolu():
    global rapor_gonderildi_bugun, acilis_bildirildi_bugun, son_gun_str
    try:
        ny_now = datetime.datetime.now(ZoneInfo("America/New_York"))
        bugun_str = ny_now.strftime("%Y-%m-%d")
        
        if son_gun_str != bugun_str:
            son_gun_str = bugun_str
            rapor_gonderildi_bugun = False
            acilis_bildirildi_bugun = False

        if ny_now.weekday() < 5:
            if ny_now.hour == 9 and ny_now.minute >= 30 and not acilis_bildirildi_bugun:
                send_telegram_msg("🔔 *NASDAQ AÇILDI!*\n_Tüm hisseler taranıyor..._")
                acilis_bildirildi_bugun = True

            if ny_now.hour >= 16 and not rapor_gonderildi_bugun:
                send_telegram_msg("🔔 *NASDAQ KAPANDI!*\n_Gün sonu raporu hazırlanıyor..._")
                gun_sonu_raporu_gonder()
                rapor_gonderildi_bugun = True
    except Exception as e:
        print(f"Zaman kontrol hatasi: {e}")

def start_scanner_loop():
    send_telegram_msg("⚡ *NASDAQ TERMINAL ONLINE* ⚡\n_Tüm NASDAQ listesi eksiksiz taranıyor..._")
    while True:
        try:
            piyasa_zaman_kontrolu()
            if is_running:
                tum_nasdaq_tarama_kesin()
        except Exception as e:
            print(f"Hata: {e}")
        time.sleep(5)

def telegram_komut_dinleme_loop():
    while True:
        try:
            check_telegram_commands()
        except Exception:
            pass
        time.sleep(2)

if __name__ == '__main__':
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    threading.Thread(target=telegram_komut_dinleme_loop, daemon=True).start()
    run_flask()
