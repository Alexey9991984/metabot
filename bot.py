import numpy as np
import os
import time
import talib
import requests
import MetaTrader5 as mt5
from dotenv import load_dotenv


import logging

# Настройка логирования
logging.basicConfig(
    filename='bot_errors.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Загрузка .env
load_dotenv()

# Переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")

SYMBOL = "EURUSD"
LOT = 0.10
TIMEFRAME = mt5.TIMEFRAME_M1
POSITION_TYPE = None  # 'buy' или 'sell'

mt5.symbol_select(SYMBOL, True)


def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("❌ Не заданы TELEGRAM_TOKEN или CHAT_ID в .env")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": message}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")


def initialize_mt5():
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        error_msg = f"❌ Ошибка инициализации MT5: {mt5.last_error()}"
        print(error_msg)
        send_telegram_message(error_msg)
        raise RuntimeError(error_msg)
    print("✅ MetaTrader 5 успешно инициализирован")
    send_telegram_message(
        "✅ Бот запущен. Ждём сигналов для открытия/закрытия сделок.")


def get_candles(symbol=SYMBOL, timeframe=TIMEFRAME, n=100):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    if rates is None or len(rates) == 0:
        msg = "❌ Ошибка получения свечей"
        print(msg)
        send_telegram_message(msg)
        return []
    return np.array(rates)['close']


def close_open_positions():
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None or len(positions) == 0:
        return

    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        send_telegram_message(
            f"❌ Не удалось получить информацию о символе {SYMBOL}")
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None or tick.bid == 0 or tick.ask == 0:
        send_telegram_message(
            "❌ Нет цен для закрытия сделки (tick None или 0)")
        return

    for pos in positions:
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": pos.volume,
            "type": order_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 50,  # Увеличено отклонение для предотвращения ошибок
            "magic": 123456,
            "comment": "Auto close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
            send_telegram_message(
                "❌ Requote при попытке закрытия позиции. Попробую снова...")
            time.sleep(2)  # Задержка перед повторной попыткой
            result = mt5.order_send(request)  # Повторная отправка ордера

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            send_telegram_message(
                f"❌ Ошибка закрытия позиции: {result.retcode}, описание: {result.comment}")
        else:
            send_telegram_message("✅ Сделка закрыта")


def open_trade(direction):
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None or tick.ask == 0 or tick.bid == 0:
        send_telegram_message("❌ Ошибка получения цен для открытия сделки")
        return

    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None or not symbol_info.visible:
        send_telegram_message(f"❌ Символ {SYMBOL} не найден или недоступен!")
        return

    price = tick.ask if direction == "buy" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL

    sl_points = 30
    tp_points = 60
    point = symbol_info.point

    sl = price - sl_points * point if direction == "buy" else price + sl_points * point
    tp = price + tp_points * point if direction == "buy" else price - tp_points * point

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 123456,
        "comment": "Auto SMA Trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        send_telegram_message(
            f"❌ Ошибка открытия сделки: {result.retcode}, ошибка: {mt5.last_error()}")
    else:
        send_telegram_message(
            f"✅ Открыта {'покупка' if direction == 'buy' else 'продажа'} по {SYMBOL} с SL: {sl:.5f}, TP: {tp:.5f}")
    # Отправка ордера
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        send_telegram_message(
            f"❌ Ошибка открытия сделки: {result.retcode}, ошибка: {mt5.last_error()}")
    else:
        send_telegram_message(
            f"✅ Открыта {'покупка' if direction == 'buy' else 'продажа'} по {SYMBOL}")


def strategy():
    global POSITION_TYPE
    close_prices = get_candles()
    if len(close_prices) == 0:
        return

    sma5 = talib.SMA(close_prices, timeperiod=5)
    sma20 = talib.SMA(close_prices, timeperiod=20)

    print(f"SMA5: {sma5[-1]:.6f}, SMA20: {sma20[-1]:.6f}")

    if sma5[-1] > sma20[-1] and sma5[-2] <= sma20[-2]:
        if POSITION_TYPE != "buy":
            close_open_positions()
            open_trade("buy")
            POSITION_TYPE = "buy"
    elif sma5[-1] < sma20[-1] and sma5[-2] >= sma20[-2]:
        if POSITION_TYPE != "sell":
            close_open_positions()
            open_trade("sell")
            POSITION_TYPE = "sell"


def run():
    try:
        initialize_mt5()
        while True:
            strategy()
            time.sleep(60)
    except Exception as e:
        error_message = f"❌ Критическая ошибка: {e}"
        send_telegram_message(error_message)
        logging.error(error_message, exc_info=True)

    # Попробуем переподключиться
    try:
        initialize_mt5()
        send_telegram_message("🔄 Попытка переподключения к MetaTrader 5...")
    except Exception as reconnect_error:
        reconnect_message = f"❌ Не удалось переподключиться: {reconnect_error}"
        send_telegram_message(reconnect_message)
        logging.error(reconnect_message, exc_info=True)

    time.sleep(60)


if __name__ == "__main__":
    run()
