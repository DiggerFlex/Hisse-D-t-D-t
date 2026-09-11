import time
import datetime
import threading
import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from flask import Flask

# RENDER UPTIME / WEB SUNUCUSU
app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ NASDAQ SCANNER ACTIVE ⚡"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# TELEGRAM VE BOT KONFİGÜRASYONU
TELEGRAM_BOT_TOKEN = "8750813780:AAHKpVFsxqT6BgYbISMZhiAp-ryzNsZ8IZY"
TELEGRAM_CHAT_ID = "7743041008"
RENDER_DEPLOY_HOOK_URL = "https://api.render.com/deploy/srv-daemtan40ujc73ft425g?key=o1ghEoCwW10"
MAX_PRICE_LIMIT = 3.00

# SİSTEM HAFIZASI VE KİLİTLER
bildirilenler = set()        
gunluk_sinyaller = {}        
last_update_id = 0         
is_running = True
lock = threading.Lock()

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
    except Exception:
        pass

def check_telegram_commands():
    global MAX_PRICE_LIMIT, last_update_id, is_running
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 2}).json()
        if "result" in res:
            for update in res["result"]:
                last_update_id = update["update_id"]
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].strip()
                    
                    if text == "/stop":
                        if is_running:
                            is_running = False
                            send_telegram_msg("🔴 BOT DURDURULDU")
                    elif text == "/start":
                        if not is_running:
                            is_running = True
                            send_telegram_msg("🟢 BOT DEVREDE")
                    elif text in ["/status", "/durum"]:
                        st = "🟢 Çalışıyor" if is_running else "🔴 Durduruldu"
                        send_telegram_msg(f"Durum: {st}\nLimit: {MAX_PRICE_LIMIT:.2f}\nSinyal Sayısı: {len(gunluk_sinyaller)}")
                    elif text in ["/stats", "/ozet"]:
                        if not gunluk_sinyaller:
                            send_telegram_msg("Bugün henüz sinyal üretilmedi.")
                        else:
                            ozet_msg = "PARA KAZANMA SANATI\nBUGÜNKÜ SİNYALLER\n"
                            for sym, data in gunluk_sinyaller.items():
                                ozet_msg += f"{sym} - Giriş: {data['entry']:.2f}\n"
                            send_telegram_msg(ozet_msg)
                    elif text == "/ping":
                        t1 = time.time()
                        send_telegram_msg("Pong!")
                    elif text == "/test":
                        process_symbol("ISPC", force_send=True)
                    elif text in ["/help", "/yardim"]:
                        help_msg = (
                            "/start - Botu başlatır\n"
                            "/stop - Botu durdurur\n"
                            "/status - Sistem durumunu gösterir\n"
                            "/stats - Bugünkü sinyalleri listeler\n"
                            "/ping - Gecikme ölçer\n"
                            "/test - Test sinyali gönderir\n"
                            "/limit [sayı] - Fiyat limitini günceller\n"
                            "/render - Render sunucusunu yeniden başlatır"
                        )
                        send_telegram_msg(help_msg)
                    elif text == "/render":
                        send_telegram_msg("Render yeniden başlatılıyor...")
                        try:
                            requests.post(RENDER_DEPLOY_HOOK_URL, timeout=10)
                        except Exception:
                            pass
                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 2:
                            try:
                                MAX_PRICE_LIMIT = float(parts[1])
                                send_telegram_msg(f"Yeni Limit: {MAX_PRICE_LIMIT:.2f}")
                            except ValueError:
                                send_telegram_msg("Geçersiz limit.")
    except Exception:
        pass

def get_penny_stocks():
    while True:
        try:
            url = "https://old.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"
            df = pd.read_csv(url, sep="|", timeout=15)
            df = df[(df['NASDAQ Symbol'].notnull()) & (df['ETF'] == 'N') & (df['Test Issue'] == 'N')]
            symbols = df['NASDAQ Symbol'].str.strip().tolist()
            return [s.replace('.', '-') for s in symbols if isinstance(s, str) and len(s) <= 5]
        except Exception:
            time.sleep(5)

def calculate_dynamic_targets(df, last_price):
    try:
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        atr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(14).mean().iloc[-1]
        if pd.isna(atr) or atr == 0:
            atr = last_price * 0.03
        tp1 = max(last_price * 1.05, last_price + (1.5 * atr))
        tp2 = max(last_price * 1.15, last_price + (3.5 * atr))
        return tp1, tp2
    except Exception:
        return last_price * 1.05, last_price * 1.15

def process_symbol(symbol, force_send=False):
    try:
        if not force_send and symbol in bildirilenler:
            return

        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m", prepost=True)
        if df.empty:
            return

        last_price = df['Close'].iloc[-1]
        last_volume = df['Volume'].iloc[-1]
        resistance = df['High'][:-1].max() if len(df) > 1 else df['High'].max()
        avg_volume = df['Volume'][:-1].mean() if len(df) > 1 else last_volume

        if not force_send:
            if last_price >= MAX_PRICE_LIMIT or last_price <= 0.05:
                return
            distance_to_resistance = (resistance - last_price) / resistance if resistance > 0 else 0.0
            vol_ratio = last_volume / avg_volume if avg_volume > 0 else 1.0
            
            # Kapanış sonrası seansı kaçırmayan hassas eşik
            if not (distance_to_resistance <= 0.035 or vol_ratio >= 1.1):
                return

        with lock:
            if symbol in bildirilenler:
                return
            bildirilenler.add(symbol)

        stop_loss = last_price * 0.85 # %15 altı stop
        tp1, tp2 = calculate_dynamic_targets(df, last_price)

        # FOTOĞRAFTAKİ BİREBİR TELEGRAM FORMATI
        msg = (
            f"PARA KAZANMA SANATI\n"
            f"{symbol}\n"
            f"KIRILIM: {last_price:.2f}\n"
            f"STOP: {stop_loss:.2f}\n"
            f"TP1: {tp1:.2f}\n"
            f"TP2: {tp2:.2f}"
        )
        
        send_telegram_msg(msg)
        
        if symbol not in gunluk_sinyaller:
            gunluk_sinyaller[symbol] = {'entry': last_price, 'tp1': tp1, 'tp2': tp2}
    except Exception:
        pass

def start_scanner_loop():
    send_telegram_msg("PARA KAZANMA SANATI\nBOT AKTİF EDİLDİ")
    while True:
        try:
            if is_running:
                symbols = get_penny_stocks()
                if symbols:
                    chunk_size = 30
                    for i in range(0, len(symbols), chunk_size):
                        if not is_running:
                            break
                        chunk = symbols[i:i + chunk_size]
                        with ThreadPoolExecutor(max_workers=5) as executor:
                            executor.map(process_symbol, chunk)
                        time.sleep(0.5)
        except Exception:
            pass
        time.sleep(5)

if __name__ == '__main__':
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    threading.Thread(target=lambda: [check_telegram_commands() or time.sleep(2) for _ in iter(int, 1)], daemon=True).start()
    run_flask()
