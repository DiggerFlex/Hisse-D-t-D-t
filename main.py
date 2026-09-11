import time
import datetime
import threading
import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ NASDAQ FULL-MARKET SCANNER ONLINE ⚡"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

TELEGRAM_BOT_TOKEN = "8750813780:AAHKpVFsxqT6BgYbISMZhiAp-ryzNsZ8IZY"
TELEGRAM_CHAT_ID = "7743041008"
MAX_PRICE_LIMIT = 100.00  # Sınırları kaldırdık

bildirilenler = set()       
is_running = True
last_update_id = 0         
lock = threading.Lock()

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
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
                        is_running = False
                        send_telegram_msg("🔴 *BOT DURDURULDU*")
                    elif text == "/start":
                        is_running = True
                        send_telegram_msg("🟢 *BOT TÜM NASDAQ TARAMASINA DEVAM EDİYOR*")
                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 2:
                            MAX_PRICE_LIMIT = float(parts[1])
                            send_telegram_msg(f"✅ *Limit Güncellendi:* `${MAX_PRICE_LIMIT:.2f}`")
    except Exception:
        pass

def get_all_nasdaq_symbols():
    try:
        url = "https://old.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"
        df = pd.read_csv(url, sep="|", timeout=15)
        df = df[(df['NASDAQ Symbol'].notnull()) & (df['ETF'] == 'N') & (df['Test Issue'] == 'N')]
        symbols = df['NASDAQ Symbol'].str.strip().tolist()
        cleaned = [s.replace('.', '-') for s in symbols if isinstance(s, str) and len(s) <= 6]
        return list(set(cleaned))
    except Exception:
        return []

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
        return tp1, ((tp1 - last_price) / last_price) * 100, tp2, ((tp2 - last_price) / last_price) * 100
    except Exception:
        return last_price * 1.07, 7.0, last_price * 1.25, 25.0

def process_symbol(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m", prepost=True)
        if df.empty or len(df) < 5:
            return

        last_price = df['Close'].iloc[-1]
        if last_price > MAX_PRICE_LIMIT or last_price <= 0.05:
            return

        last_volume = df['Volume'].iloc[-1]
        avg_vol = df['Volume'][:-1].mean()
        vol_ratio = last_volume / avg_vol if avg_vol > 0 else 1.0

        with lock:
            if symbol in bildirilenler:
                return

        price_change_pct = (last_price - df['Close'].iloc[-2]) / df['Close'].iloc[-2] * 100

        if (vol_ratio >= 1.3) or (price_change_pct >= 2.0):
            with lock:
                if symbol in bildirilenler:
                    return
                bildirilenler.add(symbol)

            tight_stop = last_price * 0.97
            tp1, tp1_pct, tp2, tp2_pct = calculate_dynamic_targets(df, last_price)
            tv_url = f"https://www.tradingview.com/chart/?symbol={symbol}"

            msg = (
                f"🚨 *NASDAQ SİNYAL: #{symbol}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *Durum:* `Anlık Patlama Yakalandı`\n"
                f"⚡ *Hacim:* `{vol_ratio:.1f}x` | *Değişim:* `+%{price_change_pct:.1f}`\n\n"
                f"💵 *Giriş:* `${last_price:.2f}`\n"
                f"🛡️ *Stop-Loss:* `${tight_stop:.2f}`\n\n"
                f"🎯 *1. Hedef (+%{tp1_pct:.1f}):* `${tp1:.2f}`\n"
                f"🎯 *2. Hedef (+%{tp2_pct:.1f}):* `${tp2:.2f}`\n\n"
                f"📈 [TradingView Grafiği]({tv_url})"
            )
            send_telegram_msg(msg)
    except Exception:
        pass

def start_scanner_loop():
    send_telegram_msg("⚡ *NASDAQ TÜM BORSASI TARAYICISI AKTİF* ⚡\n_Bütün hisseler taranıyor, kaçış yok!_")
    while True:
        if is_running:
            symbols = get_all_nasdaq_symbols()
            if symbols:
                with ThreadPoolExecutor(max_workers=60) as executor:
                    executor.map(process_symbol, symbols)
        time.sleep(15)

if __name__ == '__main__':
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    threading.Thread(target=lambda: [check_telegram_commands() or time.sleep(2) for _ in iter(int, 1)], daemon=True).start()
    run_flask()
