import talib
import numpy as np


def get_signal(close_prices):
    """
    Стратегия (обновлённая):
    - EMA10 > EMA21 — бычий тренд
    - MACD-гистограмма > 0 — импульс
    - RSI > 55 для покупки, < 45 для продажи
    - Стохастик (K > D и K > 50 для покупки, K < D и K < 50 для продажи)
    - ATR — фильтрация по волатильности
    """
    if len(close_prices) < 50:
        return None

    ema10 = talib.EMA(close_prices, timeperiod=10)
    ema21 = talib.EMA(close_prices, timeperiod=21)
    macd, macdsignal, macdhist = talib.MACD(
        close_prices, fastperiod=12, slowperiod=26, signalperiod=9)
    rsi = talib.RSI(close_prices, timeperiod=7)

    # Стохастик
    high = close_prices  # Для простоты (лучше заменить на настоящий high[])
    low = close_prices   # Аналогично
    slowk, slowd = talib.STOCH(high, low, close_prices,
                               fastk_period=14, slowk_period=3, slowk_matype=0,
                               slowd_period=3, slowd_matype=0)

    # ATR
    atr = talib.ATR(high, low, close_prices, timeperiod=14)

    # Проверка на наличие всех нужных данных
    for x in [ema10, ema21, macdhist, rsi, slowk, slowd, atr]:
        if np.isnan(x[-1]):
            return None

    # Проверка ATR — минимальный порог волатильности (например, 0.0008)
    if atr[-1] < 0.0008:
        return None

    bullish = 0
    bearish = 0

    if ema10[-1] > ema21[-1]:
        bullish += 1
    elif ema10[-1] < ema21[-1]:
        bearish += 1

    if macdhist[-1] > 0:
        bullish += 1
    elif macdhist[-1] < 0:
        bearish += 1

    if rsi[-1] > 55:
        bullish += 1
    elif rsi[-1] < 45:
        bearish += 1

    if slowk[-1] > slowd[-1] and slowk[-1] > 50:
        bullish += 1
    elif slowk[-1] < slowd[-1] and slowk[-1] < 50:
        bearish += 1

    if bullish >= 3 and bearish == 0:
        return "buy"
    elif bearish >= 3 and bullish == 0:
        return "sell"
    else:
        return None
