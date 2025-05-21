import talib
import numpy as np


def get_signal(close_prices):
    """
    Стратегия:
    - EMA10 > EMA21 — быстрый бычий тренд
    - MACD-гистограмма > 0 — подтверждение импульса
    - RSI > 55 для покупки, < 45 для продажи (более уверенный момент)
    """
    if len(close_prices) < 50:
        return None

    ema10 = talib.EMA(close_prices, timeperiod=10)
    ema21 = talib.EMA(close_prices, timeperiod=21)
    macd, macdsignal, macdhist = talib.MACD(
        close_prices, fastperiod=12, slowperiod=26, signalperiod=9)
    rsi = talib.RSI(close_prices, timeperiod=7)

    # Проверка на наличие всех нужных данных
    if any(np.isnan(x[-1]) for x in [ema10, ema21, macdhist, rsi]):
        return None

    bullish = 0
    bearish = 0

    # EMA-сигнал
    if ema10[-1] > ema21[-1]:
        bullish += 1
    elif ema10[-1] < ema21[-1]:
        bearish += 1

    # MACD-гистограмма
    if macdhist[-1] > 0:
        bullish += 1
    elif macdhist[-1] < 0:
        bearish += 1

    # RSI
    if rsi[-1] > 55:
        bullish += 1
    elif rsi[-1] < 45:
        bearish += 1

    if bullish >= 2 and bearish == 0:
        return "buy"
    elif bearish >= 2 and bullish == 0:
        return "sell"
    else:
        return None
