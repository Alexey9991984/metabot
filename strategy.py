import talib
import numpy as np
import MetaTrader5 as mt5
import logging

# Настройки стратегии
SYMBOL = "EURUSD"
TIMEFRAME = mt5.TIMEFRAME_M30
CANDLES_COUNT = 250
MIN_ATR = 0.0008  # Минимальная волатильность для торговли

# Кэш для свечей (чтобы не запрашивать каждый раз)
_last_candles_time = 0
_cached_candles = None
CACHE_DURATION = 1800  # 30 минут


def get_market_data():
    """Получение рыночных данных с кэшированием"""
    global _last_candles_time, _cached_candles
    
    current_time = mt5.symbol_info_tick(SYMBOL).time
    
    # Используем кэш если данные свежие
    if (_cached_candles is not None and 
        current_time - _last_candles_time < CACHE_DURATION):
        return _cached_candles
    
    # Получаем новые данные
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, CANDLES_COUNT)
    if rates is None or len(rates) == 0:
        logging.error("Не удалось получить исторические данные")
        return None
    
    _cached_candles = {
        'open': np.array([candle['open'] for candle in rates]),
        'high': np.array([candle['high'] for candle in rates]),
        'low': np.array([candle['low'] for candle in rates]),
        'close': np.array([candle['close'] for candle in rates]),
        'volume': np.array([candle['tick_volume'] for candle in rates])
    }
    _last_candles_time = current_time
    
    return _cached_candles


def calculate_indicators(data):
    """Расчет технических индикаторов"""
    try:
        indicators = {}
        
        # Скользящие средние
        indicators['ema10'] = talib.EMA(data['close'], timeperiod=10)
        indicators['ema21'] = talib.EMA(data['close'], timeperiod=21)
        indicators['sma50'] = talib.SMA(data['close'], timeperiod=50)
        
        # MACD
        macd, macdsignal, macdhist = talib.MACD(
            data['close'], fastperiod=12, slowperiod=26, signalperiod=9
        )
        indicators['macd'] = macd
        indicators['macd_signal'] = macdsignal
        indicators['macd_hist'] = macdhist
        
        # RSI
        indicators['rsi'] = talib.RSI(data['close'], timeperiod=14)
        indicators['rsi_fast'] = talib.RSI(data['close'], timeperiod=7)
        
        # Stochastic
        slowk, slowd = talib.STOCH(
            data['high'], data['low'], data['close'],
            fastk_period=14, slowk_period=3, slowk_matype=0,
            slowd_period=3, slowd_matype=0
        )
        indicators['stoch_k'] = slowk
        indicators['stoch_d'] = slowd
        
        # ATR для волатильности
        indicators['atr'] = talib.ATR(data['high'], data['low'], data['close'], timeperiod=14)
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = talib.BBANDS(
            data['close'], timeperiod=20, nbdevup=2, nbdevdn=2, matype=0
        )
        indicators['bb_upper'] = bb_upper
        indicators['bb_middle'] = bb_middle
        indicators['bb_lower'] = bb_lower
        
        # Williams %R
        indicators['williams_r'] = talib.WILLR(
            data['high'], data['low'], data['close'], timeperiod=14
        )
        
        return indicators
        
    except Exception as e:
        logging.error(f"Ошибка расчета индикаторов: {e}")
        return None


def check_trend_alignment(indicators):
    """Проверка согласованности трендовых индикаторов"""
    bullish_signals = 0
    bearish_signals = 0
    
    # EMA тренд (краткосрочная > долгосрочная)
    if indicators['ema10'][-1] > indicators['ema21'][-1]:
        bullish_signals += 1
    elif indicators['ema10'][-1] < indicators['ema21'][-1]:
        bearish_signals += 1
    
    # Цена относительно SMA50
    current_price = indicators['ema10'][-1]  # используем как прокси для текущей цены
    if current_price > indicators['sma50'][-1]:
        bullish_signals += 1
    elif current_price < indicators['sma50'][-1]:
        bearish_signals += 1
    
    # MACD импульс
    if indicators['macd_hist'][-1] > 0:
        bullish_signals += 1
    elif indicators['macd_hist'][-1] < 0:
        bearish_signals += 1
    
    return bullish_signals, bearish_signals


def check_momentum_oscillators(indicators):
    """Проверка импульсных осцилляторов"""
    bullish_signals = 0
    bearish_signals = 0
    
    # RSI
    rsi_current = indicators['rsi'][-1]
    if 30 < rsi_current < 70:  # не в экстремальных зонах
        if rsi_current > 55:
            bullish_signals += 1
        elif rsi_current < 45:
            bearish_signals += 1
    
    # Быстрый RSI
    rsi_fast = indicators['rsi_fast'][-1]
    if rsi_fast > 60:
        bullish_signals += 1
    elif rsi_fast < 40:
        bearish_signals += 1
    
    # Stochastic
    stoch_k = indicators['stoch_k'][-1]
    stoch_d = indicators['stoch_d'][-1]
    
    if stoch_k > stoch_d and stoch_k > 50:
        bullish_signals += 1
    elif stoch_k < stoch_d and stoch_k < 50:
        bearish_signals += 1
    
    # Williams %R
    williams = indicators['williams_r'][-1]
    if williams > -50:
        bullish_signals += 1
    elif williams < -50:
        bearish_signals += 1
    
    return bullish_signals, bearish_signals


def check_volatility_filter(indicators):
    """Проверка фильтра волатильности"""
    atr_current = indicators['atr'][-1]
    
    # Проверяем минимальную волатильность
    if atr_current < MIN_ATR:
        return False, "Низкая волатильность"
    
    # Проверяем, что волатильность не слишком высокая (избегаем новостей)
    atr_avg = np.mean(indicators['atr'][-10:])  # средняя за 10 периодов
    if atr_current > atr_avg * 2:
        return False, "Аномально высокая волатильность"
    
    return True, f"ATR: {atr_current:.5f}"


def get_signal():
    """
    Главная функция получения торгового сигнала
    
    Логика стратегии:
    1. Получаем рыночные данные
    2. Рассчитываем технические индикаторы
    3. Проверяем согласованность трендовых сигналов
    4. Проверяем импульсные осцилляторы
    5. Применяем фильтр волатильности
    6. Принимаем решение на основе весов сигналов
    """
    try:
        # Получение данных
        market_data = get_market_data()
        if market_data is None:
            return None
        
        # Проверка достаточности данных
        if len(market_data['close']) < 100:
            logging.warning("Недостаточно исторических данных")
            return None
        
        # Расчет индикаторов
        indicators = calculate_indicators(market_data)
        if indicators is None:
            return None
        
        # Проверка валидности индикаторов
        required_indicators = ['ema10', 'ema21', 'macd_hist', 'rsi', 'stoch_k', 'stoch_d', 'atr']
        for indicator in required_indicators:
            if np.isnan(indicators[indicator][-1]):
                logging.warning(f"Недопустимое значение индикатора: {indicator}")
                return None
        
        # Фильтр волатильности
        volatility_ok, vol_msg = check_volatility_filter(indicators)
        if not volatility_ok:
            logging.info(f"Сигнал отклонен: {vol_msg}")
            return None
        
        # Анализ трендовых сигналов
        trend_bullish, trend_bearish = check_trend_alignment(indicators)
        
        # Анализ импульсных сигналов
        momentum_bullish, momentum_bearish = check_momentum_oscillators(indicators)
        
        # Подсчет общих сигналов
        total_bullish = trend_bullish + momentum_bullish
        total_bearish = trend_bearish + momentum_bearish
        
        # Логирование состояния индикаторов
        logging.info(
            f"Анализ сигналов - "
            f"Бычьи: {total_bullish} (тренд: {trend_bullish}, импульс: {momentum_bullish}), "
            f"Медвежьи: {total_bearish} (тренд: {trend_bearish}, импульс: {momentum_bearish}), "
            f"RSI: {indicators['rsi'][-1]:.2f}, "
            f"Stoch: {indicators['stoch_k'][-1]:.2f}, "
            f"{vol_msg}"
        )
        
        # Принятие решения (требуем минимум 4 сигнала из 7 возможных)
        min_signals = 4
        
        if total_bullish >= min_signals and total_bullish > total_bearish:
            logging.info(f"🟢 СИГНАЛ: BUY (сила: {total_bullish}/{total_bullish + total_bearish})")
            return 'buy'
        elif total_bearish >= min_signals and total_bearish > total_bullish:
            logging.info(f"🔴 СИГНАЛ: SELL (сила: {total_bearish}/{total_bullish + total_bearish})")
            return 'sell'
        else:
            logging.info("⚪ Недостаточно сильный сигнал для торговли")
            return None
            
    except Exception as e:
        logging.error(f"Ошибка в стратегии: {e}", exc_info=True)
        return None


def get_signal_strength():
    """Дополнительная функция для получения силы сигнала (для отладки)"""
    market_data = get_market_data()
    if market_data is None:
        return None
    
    indicators = calculate_indicators(market_data)
    if indicators is None:
        return None
    
    trend_bullish, trend_bearish = check_trend_alignment(indicators)
    momentum_bullish, momentum_bearish = check_momentum_oscillators(indicators)
    
    return {
        'trend_bullish': trend_bullish,
        'trend_bearish': trend_bearish,
        'momentum_bullish': momentum_bullish,
        'momentum_bearish': momentum_bearish,
        'rsi': indicators['rsi'][-1],
        'macd_hist': indicators['macd_hist'][-1],
        'atr': indicators['atr'][-1]
    }