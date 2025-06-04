from strategy import get_signal
from datetime import datetime, time as dt_time
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

# Настройки управления рисками
RISK_PERCENT = 1.0          # Процент риска от депозита
USE_TRAILING_STOP = True    # Использовать трейлинг стоп
USE_PARTIAL_CLOSE = True    # Использовать частичное закрытие
PARTIAL_CLOSE_PIPS = 30     # При скольких пипсах закрывать частично

# Торговые часы (GMT)
TRADING_START = dt_time(6, 0)   # 06:00
TRADING_END = dt_time(22, 0)    # 22:00
PING_INTERVAL = 10800  # 3 часа в секундах


def send_telegram_message(message):
    """Отправка сообщения в Telegram"""
    print(f"[Telegram] {message}")
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.get(url, params={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        logging.error(f"Ошибка Telegram: {e}")


def initialize_mt5():
    """Инициализация MT5"""
    print("🔄 Инициализация MetaTrader 5...")
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        msg = f"❌ Ошибка инициализации MT5: {mt5.last_error()}"
        send_telegram_message(msg)
        raise RuntimeError(msg)
    
    # Выбор символа
    if not mt5.symbol_select(SYMBOL, True):
        msg = f"❌ Не удалось выбрать символ {SYMBOL}"
        send_telegram_message(msg)
        raise RuntimeError(msg)
    
    print("✅ MetaTrader 5 инициализирован.")
    send_telegram_message("✅ Бот запущен. Ожидание сигналов...")


def is_trading_time():
    """Проверка торгового времени и выходных"""
    now = datetime.now()
    current_time = now.time()
    weekday = now.weekday()
    
    # Проверка выходных (суббота=5, воскресенье=6)
    if weekday >= 5:
        return False
        
    # Проверка торговых часов
    return TRADING_START <= current_time < TRADING_END


def get_current_position():
    """Получение текущей позиции"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return None
    return "buy" if positions[0].type == mt5.POSITION_TYPE_BUY else "sell"


def close_open_positions():
    """Закрытие всех открытых позиций"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return True

    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick or tick.bid == 0 or tick.ask == 0:
        send_telegram_message("❌ Нет данных для закрытия сделки")
        return False

    success = True
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
            logging.info(f"Позиция закрыта: {pos.ticket}")
        else:
            send_telegram_message(f"❌ Ошибка закрытия: {result.comment}")
            logging.error(f"Ошибка закрытия позиции {pos.ticket}: {result.comment}")
            success = False
    
    return success


def validate_lot_size():
    """Проверка допустимого размера лота"""
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None:
        return False, f"❌ Не удалось получить информацию о символе {SYMBOL}"

    min_vol = symbol_info.volume_min
    max_vol = symbol_info.volume_max
    step_vol = symbol_info.volume_step

    if (LOT < min_vol or LOT > max_vol or 
        round((LOT - min_vol) / step_vol) * step_vol + min_vol - LOT > 1e-8):
        return False, f"❌ Недопустимый объём сделки: {LOT}. Допустимо от {min_vol} до {max_vol} с шагом {step_vol}"
    
    return True, ""


def calculate_dynamic_sl_tp(signal, entry_price, atr_value):
    """Расчет динамических стоп-лосса и тейк-профита на основе ATR"""
    # Базовые множители ATR
    sl_atr_multiplier = 1.5  # Стоп-лосс = 1.5 * ATR
    tp_atr_multiplier = 2.5  # Тейк-профит = 2.5 * ATR
    
    # Минимальные и максимальные значения (в пипсах для EURUSD)
    min_sl_pips = 15  # минимум 15 пипсов
    max_sl_pips = 50  # максимум 50 пипсов
    min_tp_pips = 20  # минимум 20 пипсов
    max_tp_pips = 80  # максимум 80 пипсов
    
    # Конвертация пипсов в цену для EURUSD (1 пип = 0.0001)
    pip_value = 0.0001
    
    # Расчет SL и TP на основе ATR
    atr_sl_distance = atr_value * sl_atr_multiplier
    atr_tp_distance = atr_value * tp_atr_multiplier
    
    # Ограничиваем минимальными и максимальными значениями
    sl_distance = max(min_sl_pips * pip_value, min(atr_sl_distance, max_sl_pips * pip_value))
    tp_distance = max(min_tp_pips * pip_value, min(atr_tp_distance, max_tp_pips * pip_value))
    
    if signal == 'buy':
        sl = entry_price - sl_distance
        tp = entry_price + tp_distance
    else:  # sell
        sl = entry_price + sl_distance
        tp = entry_price - tp_distance
    
    return sl, tp, sl_distance / pip_value, tp_distance / pip_value


def get_current_atr():
    """Получение текущего значения ATR"""
    try:
        from strategy import get_market_data, calculate_indicators
        
        market_data = get_market_data()
        if market_data is None:
            return 0.0020  # значение по умолчанию
        
        indicators = calculate_indicators(market_data)
        if indicators is None or np.isnan(indicators['atr'][-1]):
            return 0.0020  # значение по умолчанию
        
        return indicators['atr'][-1]
    except:
        return 0.0020  # значение по умолчанию в случае ошибки


def open_trade(signal, risk_percent=1.0):
    """
    Открытие сделки с динамическими SL/TP
    
    Args:
        signal: 'buy' или 'sell'
        risk_percent: процент риска от депозита (по умолчанию 1%)
    """
    # Проверка размера лота
    is_valid, error_msg = validate_lot_size()
    if not is_valid:
        send_telegram_message(error_msg)
        logging.error(error_msg)
        return False

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        send_telegram_message("❌ Не удалось получить цену тикера")
        logging.error("Не удалось получить цену тикера")
        return False

    # Получение текущего ATR для расчета динамических уровней
    current_atr = get_current_atr()
    
    price = tick.ask if signal == 'buy' else tick.bid
    sl, tp, sl_pips, tp_pips = calculate_dynamic_sl_tp(signal, price, current_atr)
    
    order_type = mt5.ORDER_TYPE_BUY if signal == 'buy' else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 123456,
        "comment": f"Bot {signal} ATR-based",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        error_msg = f"❌ Ошибка при открытии позиции: {result.retcode} - {result.comment}"
        send_telegram_message(error_msg)
        logging.error(error_msg)
        return False
    else:
        rr_ratio = tp_pips / sl_pips  # Risk-Reward соотношение
        success_msg = (f"✅ Сделка открыта: {signal.upper()} {SYMBOL}\n"
                      f"💰 Цена: {price:.5f}\n"
                      f"🛑 SL: {sl:.5f} (-{sl_pips:.1f} пипсов)\n"
                      f"🎯 TP: {tp:.5f} (+{tp_pips:.1f} пипсов)\n"
                      f"📊 R/R: 1:{rr_ratio:.1f}\n"
                      f"📈 ATR: {current_atr:.5f}")
        
        send_telegram_message(success_msg)
        logging.info(f"Открыта позиция {signal.upper()}: вход={price:.5f}, SL={sl:.5f} ({sl_pips:.1f}п), TP={tp:.5f} ({tp_pips:.1f}п), R/R=1:{rr_ratio:.1f}")
        return True


def update_trailing_stop():
    """Обновление трейлинг стоп-лосса для открытых позиций"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return

    current_atr = get_current_atr()
    trailing_distance = current_atr * 1.0  # Трейлинг дистанция = 1 ATR
    min_trailing_pips = 10  # Минимум 10 пипсов
    pip_value = 0.0001
    
    trailing_distance = max(trailing_distance, min_trailing_pips * pip_value)

    for pos in positions:
        try:
            tick = mt5.symbol_info_tick(SYMBOL)
            if not tick:
                continue

            current_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            current_sl = pos.sl
            
            # Рассчитываем новый SL
            if pos.type == mt5.POSITION_TYPE_BUY:
                # Для покупки: новый SL выше текущего
                new_sl = current_price - trailing_distance
                should_update = new_sl > current_sl and new_sl < current_price
            else:
                # Для продажи: новый SL ниже текущего  
                new_sl = current_price + trailing_distance
                should_update = new_sl < current_sl and new_sl > current_price

            if should_update:
                # Модифицируем позицию
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": SYMBOL,
                    "position": pos.ticket,
                    "sl": new_sl,
                    "tp": pos.tp,  # Оставляем TP без изменений
                }
                
                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    move_pips = abs(new_sl - current_sl) / pip_value
                    direction = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    msg = f"🔄 Трейлинг SL обновлен ({direction}): {current_sl:.5f} → {new_sl:.5f} (+{move_pips:.1f} пипсов)"
                    send_telegram_message(msg)
                    logging.info(f"Обновлен трейлинг SL для позиции {pos.ticket}: {new_sl:.5f}")
                else:
                    logging.warning(f"Не удалось обновить SL для позиции {pos.ticket}: {result.comment}")
                    
        except Exception as e:
            logging.error(f"Ошибка обновления трейлинг SL для позиции {pos.ticket}: {e}")


def check_partial_close():
    """Проверка возможности частичного закрытия позиций"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return

    for pos in positions:
        try:
            # Рассчитываем текущую прибыль в пипсах
            tick = mt5.symbol_info_tick(SYMBOL)
            if not tick:
                continue
                
            pip_value = 0.0001
            current_price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask
            
            if pos.type == mt5.POSITION_TYPE_BUY:
                profit_pips = (current_price - pos.price_open) / pip_value
            else:
                profit_pips = (pos.price_open - current_price) / pip_value
            
            # Если прибыль больше 30 пипсов и позиция не была частично закрыта
            if profit_pips > 30 and pos.volume >= LOT:
                partial_volume = round(pos.volume * 0.5, 2)  # Закрываем 50%
                
                order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                close_price = tick.bid if order_type == mt5.ORDER_TYPE_SELL else tick.ask
                
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": SYMBOL,
                    "volume": partial_volume,
                    "type": order_type,
                    "position": pos.ticket,
                    "price": close_price,
                    "deviation": 50,
                    "magic": 123456,
                    "comment": "Partial close +30 pips",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                
                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    direction = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    msg = f"💰 Частичное закрытие ({direction}): 50% позиции при +{profit_pips:.1f} пипсах"
                    send_telegram_message(msg)
                    logging.info(f"Частично закрыта позиция {pos.ticket}: {partial_volume} лотов при +{profit_pips:.1f} пипсах")
                    
        except Exception as e:
            logging.error(f"Ошибка частичного закрытия позиции {pos.ticket}: {e}")


def get_strategy_signal():
    """Получение сигнала от стратегии"""
    try:
        return get_signal()
    except Exception as e:
        logging.error(f"Ошибка получения сигнала: {e}")
        return None


def run():
    """Основной цикл бота"""
    global POSITION_TYPE
    
    try:
        initialize_mt5()
        last_ping_time = time.time()

        while True:
            try:
                # Обновление состояния позиции
                POSITION_TYPE = get_current_position()
                
                # Пинг каждые 3 часа
                if time.time() - last_ping_time >= PING_INTERVAL:
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    send_telegram_message(f"✅ Бот активен. Время: {now}")
                    logging.info("Ping отправлен")
                    last_ping_time = time.time()

                # Проверка торгового времени
                if not is_trading_time():
                    current_hour = datetime.now().hour
                    weekday = datetime.now().weekday()
                    
                    if weekday >= 5:
                        logging.info("🌙 Выходные дни. Торговля приостановлена.")
                    else:
                        logging.info(f"🌙 Вне торгового времени ({current_hour}:xx). Ожидание...")
                    
                    time.sleep(300)  # 5 минут
                    continue

                # Управление существующими позициями
                if POSITION_TYPE is not None:
                    update_trailing_stop()  # Обновляем трейлинг стоп
                    check_partial_close()   # Проверяем частичное закрытие

                # Получение сигнала стратегии
                signal = get_strategy_signal()
                
                # Логирование текущего состояния
                logging.info(f"🔍 Сигнал стратегии: {signal}, Текущая позиция: {POSITION_TYPE}")

                # Обработка сигнала
                if signal and signal != POSITION_TYPE:
                    logging.info("📈 Новый сигнал! Выполняется смена позиции...")
                    
                    # Закрытие текущих позиций
                    if POSITION_TYPE is not None:
                        if not close_open_positions():
                            logging.warning("⚠️ Не удалось закрыть все позиции")
                            time.sleep(30)
                            continue
                    
                    # Небольшая пауза после закрытия
                    time.sleep(2)
                    
                    # Открытие новой позиции
                    if open_trade(signal):
                        POSITION_TYPE = signal
                        logging.info(f"✅ Новая позиция открыта: {POSITION_TYPE}")
                    else:
                        logging.warning("⚠️ Не удалось открыть новую позицию")
                
                elif signal == POSITION_TYPE and POSITION_TYPE is not None:
                    logging.info("➡️ Сигнал подтверждает текущую позицию")
                
                elif not signal:
                    logging.info("⚪ Нет торгового сигнала")
                
                # Пауза между проверками
                time.sleep(30)  # 30 секунд

            except Exception as e:
                error_msg = f"❌ Ошибка в цикле: {e}"
                logging.error(error_msg, exc_info=True)
                send_telegram_message(error_msg)
                time.sleep(60)

    except Exception as e:
        critical_error = f"❌ Критическая ошибка: {e}"
        logging.error(critical_error, exc_info=True)
        send_telegram_message(critical_error)
        
        # Попытка переподключения
        time.sleep(300)  # 5 минут
        logging.info("🔄 Попытка перезапуска...")
        run()
    
    finally:
        if mt5.initialize():
            mt5.shutdown()
            logging.info("🔌 MT5 отключен")


if __name__ == "__main__":
    run()