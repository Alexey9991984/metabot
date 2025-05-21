from strategy import get_signal
from datetime import datetime
import numpy as np
import os
import time
import requests
import MetaTrader5 as mt5
from dotenv import load_dotenv
import logging

# Настройка логирования
logging.basicConfig(
    filename="bot_log.txt",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.info("🚀 Бот запущен.")

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_PASSWORD = os.getenv("MT5_PASSWORD")
MT5_SERVER = os.getenv("MT5_SERVER")

SYMBOL = "EURUSD"
LOT = 0.10
TIMEFRAME = mt5.TIMEFRAME_M30
POSITION_TYPE = None

mt5.symbol_select(SYMBOL, True)


def send_telegram_message(message):
    print(f"[Telegram] {message}")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.get(url, params={"chat_id": CHAT_ID, "text": message})
    except Exception as e:
        print(f"Ошибка Telegram: {e}")


def initialize_mt5():
    print("🔄 Инициализация MetaTrader 5...")
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        msg = f"❌ Ошибка инициализации MT5: {mt5.last_error()}"
        send_telegram_message(msg)
        raise RuntimeError(msg)
    print("✅ MetaTrader 5 инициализирован.")
    send_telegram_message("✅ Бот запущен. Ожидание сигналов...")


def get_candles(symbol=SYMBOL, timeframe=TIMEFRAME, n=250):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    if rates is None or len(rates) == 0:
        send_telegram_message("❌ Ошибка получения свечей")
        return []
    return np.array(rates)['close']


def close_open_positions():
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick or tick.bid == 0 or tick.ask == 0:
        send_telegram_message("❌ Нет данных для закрытия сделки")
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
            "deviation": 50,
            "magic": 123456,
            "comment": "Auto close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            send_telegram_message("✅ Позиция закрыта")
        else:
            send_telegram_message(f"❌ Ошибка закрытия: {result.comment}")


def open_trade(signal):
    lot = LOT
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        send_telegram_message(
            f"❌ Не удалось получить информацию о символе {SYMBOL}")
        logging.error(f"Не удалось получить информацию о символе {SYMBOL}")
        return False

    min_vol = symbol_info.volume_min
    max_vol = symbol_info.volume_max
    step_vol = symbol_info.volume_step

    if (
        lot < min_vol
        or lot > max_vol
        or round((lot - min_vol) / step_vol) * step_vol + min_vol - lot > 1e-8
    ):
        send_telegram_message(
            f"❌ Недопустимый объём сделки: {lot}. Допустимо от {min_vol} до {max_vol} с шагом {step_vol}"
        )
        logging.error(
            f"Неверный объем: {lot} (min={min_vol}, max={max_vol}, step={step_vol})"
        )
        return False

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        send_telegram_message("❌ Не удалось получить цену тикера")
        logging.error("Не удалось получить цену тикера")
        return False

    price = tick.ask if signal == 'buy' else tick.bid
    sl = price - 0.0015 if signal == 'buy' else price + 0.0015
    tp = price + 0.0015 if signal == 'buy' else price - 0.0015
    deviation = 10
    order_type = mt5.ORDER_TYPE_BUY if signal == 'buy' else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": deviation,
        "magic": 123456,
        "comment": "Python script open",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        send_telegram_message(
            f"❌ Ошибка при открытии позиции: {result.retcode}")
        logging.error(f"Ошибка открытия позиции: {result.retcode}")
        return False
    else:
        send_telegram_message(
            f"✅ Сделка открыта: {signal.upper()} {SYMBOL} по цене {price}")
        logging.info(
            f"Сделка открыта: {signal.upper()} {SYMBOL} по цене {price}")
        return True


def strategy():
    close_prices = get_candles()
    if len(close_prices) < 200:
        return None
    return get_signal(close_prices)


def run():
    global POSITION_TYPE
    try:
        initialize_mt5()

        last_ping_time = time.time()  # для отслеживания времени пинга

        while True:
            # Обновление состояния позиции
            positions = mt5.positions_get(symbol=SYMBOL)
            if not positions:
                POSITION_TYPE = None
            else:
                POSITION_TYPE = "buy" if positions[0].type == mt5.POSITION_TYPE_BUY else "sell"

            # Пинг каждый час
            if time.time() - last_ping_time >= 3600:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                send_telegram_message(f"✅ Бот жив. Время: {now}")
                last_ping_time = time.time()

            # Получение сигнала

            # Проверка времени (с 6:00 до 22:00)
            current_hour = datetime.now().hour
            if 6 <= current_hour < 22:
                signal = strategy()

            # Логирование полученного сигнала и текущей позиции
                logging.info(
                    f"🔍 Получен сигнал: {signal}, Текущая позиция: {POSITION_TYPE}")

            if signal and signal != POSITION_TYPE:
                logging.info(
                    "📈 Новый сигнал отличается от текущей позиции. Закрытие и открытие сделки...")
                close_open_positions()
                if open_trade(signal):
                    POSITION_TYPE = signal
                    logging.info(f"✅ Новая позиция открыта: {POSITION_TYPE}")
                else:
                    logging.warning("⚠️ Не удалось открыть новую позицию.")
            else:
                logging.info(
                    "🌙 Вне торгового времени (с 22:00 до 6:00). Ожидание...")

            if signal and signal != POSITION_TYPE:
                logging.info(
                    "📈 Новый сигнал отличается от текущей позиции. Закрытие и открытие сделки...")
                close_open_positions()
                if open_trade(signal):
                    POSITION_TYPE = signal
                    logging.info(f"✅ Новая позиция открыта: {POSITION_TYPE}")
                else:
                    logging.warning("⚠️ Не удалось открыть новую позицию.")
            time.sleep(10)

    except Exception as e:
        error_msg = f"❌ Критическая ошибка: {e}"
        logging.error(error_msg, exc_info=True)
        send_telegram_message(error_msg)
        time.sleep(60)


if __name__ == "__main__":
    run()
