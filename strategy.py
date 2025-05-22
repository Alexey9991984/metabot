import talib
import numpy as np
import MetaTrader5 as mt5


def get_signal(close_prices):
    """
    Стратегия (EMA, MACD, RSI, Stochastic, ATR):
    - EMA10 > EMA21 — тренд
    - MACD-гистограмма > 0 — импульс
    - RSI > 55 для покупки, < 45 для продажи
    - Стохастик (K > D и K > 50 для buy, K < D и K < 50 для sell)
    - ATR — фильтрация по волатильности (например, > 0.0008)
    """
    if len(close_prices) < 50:
        return None

    # Получаем последние 100 свечей с high/low
    candles = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M30, 0, 100)
    if candles is None or len(candles) == 0:
        return None

    high = np.array([candle['high'] for candle in candles])
    low = np.array([candle['low'] for candle in candles])
    close = np.array([candle['close'] for candle in candles])

    ema10 = talib.EMA(close, timeperiod=10)
    ema21 = talib.EMA(close, timeperiod=21)
    macd, macdsignal, macdhist = talib.MACD(
        close, fastperiod=12, slowperiod=26, signalperiod=9)
    rsi = talib.RSI(close, timeperiod=7)
    slowk, slowd = talib.STOCH(
        high, low, close,
        fastk_period=14, slowk_period=3, slowk_matype=0,
        slowd_period=3, slowd_matype=0
    )
    atr = talib.ATR(high, low, close, timeperiod=14)

    # Проверка, что все индикаторы корректно рассчитались
    for x in [ema10, ema21, macdhist, rsi, slowk, slowd, atr]:
        if np.isnan(x[-1]):
            return None

    # Волатильность фильтр
    if atr[-1] < 0.0008:
        return None

    bullish = 0
    bearish = 0

    # EMA
    if ema10[-1] > ema21[-1]:
        bullish += 1
    elif ema10[-1] < ema21[-1]:
        bearish += 1

    # MACD
    if macdhist[-1] > 0:
        bullish += 1
    elif macdhist[-1] < 0:
        bearish += 1

    # RSI
    if rsi[-1] > 55:
        bullish += 1
    elif rsi[-1] < 45:
        bearish += 1

    # Стохастик
    if slowk[-1] > slowd[-1] and slowk[-1] > 50:
        bullish += 1
    elif slowk[-1] < slowd[-1] and slowk[-1] < 50:
        bearish += 1

    # Решение
    if bullish >= 3:
        return 'buy'
    elif bearish >= 3:
        return 'sell'
    return None
