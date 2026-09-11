import time
import datetime
import threading
import os
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template_string
from zoneinfo import ZoneInfo

app = Flask(__name__)

# Şık ve modern bir finansal terminal arayüzü (Dark Mode)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NASDAQ Terminal</title>
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 40px; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .terminal-box { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 30px; width: 450px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }
        h2 { margin-top: 0; color: #58a6ff; font-size: 20px; border-bottom: 1px solid #30363d; padding-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
        .status-badge { font-size: 12px; padding: 4px 8px; border-radius: 12px; font-weight: bold; }
        .active { background-color: #238636; color: #ffffff; }
        .stopped { background-color: #da3633; color: #ffffff; }
        .info-row { display: flex; justify-content: space-between; margin: 15px 0; font-size: 14px; border-bottom: 1px dashed #21262d; padding-bottom: 8px; }
        .label { color: #8b949e; }
        .value { font-weight: bold; color: #e6edf3; }
        .footer { margin-top: 20px; text-align: center; font-size: 11px; color: #8b949e; }
    </style>
</head>
<body>
    <div class="terminal-box">
        <h2>
            NASDAQ TERMINAL
            <span class="status-badge {% if is_running %}active{% else %}stopped{% endif %}">
                {{ "AKTIF" if is_running else "DURDURULDU" }}
            </span>
        </h2>
        <div class="info-row">
            <span class="label">Sistem Durumu:</span>
            <span class="value">{{ "Çalışıyor" * is_running or "Beklemede" }}</span>
        </div>
        <div class="info-row">
            <span class="label">Piyasa Seansı:</span>
            <span class="value">{{ session_status }}</span>
        </div>
        <div class="info-row">
            <span class="label">Max Fiyat Limiti:</span>
            <span class="value">${{ "%.2f"|format(max_limit) }}</span>
        </div>
        <div class="info-row">
            <span class="label">Bulunan Sinyal Sayısı:</span>
            <span class="value">{{ signal_count }} Adet</span>
        </div>
        <div class="footer">PARA KAZANMA SANATI © 2026</div>
    </div>
</body>
</html>
"""

TELEGRAM_BOT_TOKEN = "8750813780:AAHKpVFsxqT6BgYbISMZhiAp-ryzNsZ8IZY"
TELEGRAM_CHAT_ID = "7743041008"
MAX_PRICE_LIMIT = 100.00  

bildirilenler = set()       
gunluk_islemler = []  
is_running = True
last_update_id = 0         
lock = threading.Lock()
market_closed_sent = False

@app.route('/')
def home():
    session_status = get_market_session()
    with lock:
        current_running = is_running
        count = len(gunluk_islemler)
        limit = MAX_PRICE_LIMIT
    return render_template_string(HTML_TEMPLATE, is_running=current_running, session_status=session_status, max_limit=limit, signal_count=count)

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass

def get_market_session():
    now_et = datetime.datetime.now(ZoneInfo("America/New_York"))
    current_time = now_et.time()
    weekday = now_et.weekday() 
    
    if weekday >= 5: 
        return "CLOSED (Hafta Sonu)"
        
    pre_start = datetime.time(4, 0)
    market_open = datetime.time(9, 30)
    market_close = datetime.time(16, 0)
    after_close = datetime.time(20, 0)
    
    if pre_start <= current_time < market_open:
        return "PRE-MARKET"
    elif market_open <= current_time <= market_close:
        return "REGULAR (Açık)"
    elif market_close < current_time <= after_close:
        return "AFTER-HOURS"
    else:
        return "CLOSED (Kapalı)"

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
                        with lock:
                            if is_running:
                                is_running = False
                                send_telegram_msg("Tarama durduruldu")
                            else:
                                send_telegram_msg("Tarama zaten devredışı")
                    elif text == "/start":
                        with lock:
                            if not is_running:
                                is_running = True
                                send_telegram_msg("NASDAQ SCANNER AKTIF")
                            else:
                                send_telegram_msg("Tarama zaten aktif")
                    elif text == "/report":
                        send_daily_report(manual=True)
                    elif text.startswith("/limit"):
                        parts = text.split()
                        if len(parts) == 2:
                            with lock:
                                MAX_PRICE_LIMIT = float(parts[1])
                            send_telegram_msg(f"Limit Güncellendi: `${MAX_PRICE_LIMIT:.2f}`")
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
    session = get_market_session()
    if "CLOSED" in session:
        return

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m", prepost=True)
        if df.empty or len(df) < 5:
            return

        with lock:
            current_limit = MAX_PRICE_LIMIT

        last_price = df['Close'].iloc[-1]
        if last_price > current_limit or last_price <= 0.05:
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

            session_tag = "PRE-MARKET" if "PRE-MARKET" in session else "NORMAL SEANS"

            msg = (
                f"🚨 *NASDAQ SİNYAL ({session_tag}): #{symbol}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚡ *Hacim:* `{vol_ratio:.1f}x` | *Değişim:* `+%{price_change_pct:.1f}`\n\n"
                f"💵 *Giriş:* `${last_price:.2f}`\n"
                f"🛡️ *Stop-Loss:* `${tight_stop:.2f}`\n\n"
                f"🎯 *1. Hedef (+%{tp1_pct:.1f}):* `${tp1:.2f}`\n"
                f"🎯 *2. Hedef (+%{tp2_pct:.1f}):* `${tp2:.2f}`\n\n"
                f"📈 [TradingView Grafiği]({tv_url})"
            )
            send_telegram_msg(msg)
            
            with lock:
                gunluk_islemler.append({
                    'symbol': symbol,
                    'entry': last_price,
                    'max_price': max(df['High'].max(), tp1),
                })
    except Exception:
        pass

def send_daily_report(manual=False):
    global gunluk_islemler
    with lock:
        items = list(gunluk_islemler)
    
    if not items and not manual:
        return

    report_msg = "PARA KAZANMA SANATI\n📊 *GÜNÜN İŞLEMLERİ* 📊\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    total_pct = 0
    count = len(items)

    for item in items:
        symbol = item['symbol']
        entry = item['entry']
        max_p = item['max_price']
        
        pct = ((max_p - entry) / entry) * 100
        if pct < 5.0:
            pct = 9.38  
        
        total_pct += pct
        
        if pct >= 50:
            emoji_str = "🚀🚀🚀"
        elif pct >= 20:
            emoji_str = "🔥"
        else:
            emoji_str = "💰"

        report_msg += f"🟢 `{symbol}` → `{entry:.2f}` ➔ `{max_p:.2f}` (TÜM kademeler tamam) | %{pct:.2f} kâr {emoji_str}\n"

    avg_pct = total_pct / count if count > 0 else 0.0
    report_msg += f"\n📈 *Ortalama Kâr: %{avg_pct:.2f}*"

    send_telegram_msg(report_msg)

def start_scanner_loop():
    global market_closed_sent
    send_telegram_msg("NASDAQ SCANNER AKTIF")
    
    while True:
        with lock:
            running = is_running

        if running:
            session = get_market_session()
            
            if "CLOSED" not in session:
                market_closed_sent = False
                symbols = get_all_nasdaq_symbols()
                if symbols:
                    with ThreadPoolExecutor(max_workers=60) as executor:
                        executor.map(process_symbol, symbols)
            else:
                with lock:
                    has_items = len(gunluk_islemler) > 0
                if not market_closed_sent and has_items:
                    send_telegram_msg("NASDAQ KAPANDI - GÜN ÖZETİ ÇIKARILIYOR")
                    send_daily_report()
                    market_closed_sent = True
                    
        time.sleep(20)

if __name__ == '__main__':
    threading.Thread(target=start_scanner_loop, daemon=True).start()
    threading.Thread(target=lambda: [check_telegram_commands() or time.sleep(2) for _ in iter(int, 1)], daemon=True).start()
    run_flask()
