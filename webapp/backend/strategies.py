"""
전략 정의 (backtesting.py 기반, 파이썬 방식)
=============================================
- 내장 전략은 BUILTIN 레지스트리에 등록 → 프론트 드롭다운으로 노출
- 사용자가 직접 파이썬 Strategy 코드를 입력하면 load_custom() 으로 로딩

모두 KOSPI 현실을 반영해 롱 온리(공매도 없음)로 작성.
기존 tradingview/*.pine 전략은 이 파일에 Strategy 서브클래스로 이식하면 됨.
"""
import numpy as np
import pandas as pd
from backtesting import Strategy
from backtesting.lib import crossover


def SMA(arr, n):
    return pd.Series(arr).rolling(int(n)).mean()


def _rsi(arr, n):
    s = pd.Series(arr)
    d = s.diff()
    up = d.clip(lower=0).rolling(int(n)).mean()
    dn = (-d.clip(upper=0)).rolling(int(n)).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50).values


# ----------------------------------------------------------------------
# 내장 전략들
# ----------------------------------------------------------------------
class SmaCross(Strategy):
    """이동평균 골든/데드 크로스 (롱 온리)."""
    n1 = 10
    n2 = 30

    def init(self):
        self.sma1 = self.I(SMA, self.data.Close, self.n1)
        self.sma2 = self.I(SMA, self.data.Close, self.n2)

    def next(self):
        if crossover(self.sma1, self.sma2):
            self.buy()
        elif crossover(self.sma2, self.sma1):
            self.position.close()


class DonchianBreakout(Strategy):
    """N일 신고가 돌파 진입, M일 신저가 이탈 청산."""
    entry_n = 20
    exit_n = 10

    def init(self):
        h = pd.Series(self.data.High)
        l = pd.Series(self.data.Low)
        # 당일 제외한 과거 채널
        self.upper = self.I(lambda: h.rolling(self.entry_n).max().shift(1).values)
        self.lower = self.I(lambda: l.rolling(self.exit_n).min().shift(1).values)

    def next(self):
        price = self.data.Close[-1]
        if not self.position and price > self.upper[-1]:
            self.buy()
        elif self.position and price < self.lower[-1]:
            self.position.close()


class Rsi2(Strategy):
    """RSI(2) 과매도 반등 + 장기추세 필터 (평균회귀)."""
    rsi_n = 2
    trend_n = 200
    buy_th = 10
    sell_th = 70

    def init(self):
        self.rsi = self.I(_rsi, self.data.Close, self.rsi_n)
        self.trend = self.I(SMA, self.data.Close, self.trend_n)

    def next(self):
        price = self.data.Close[-1]
        if not self.position and price > self.trend[-1] and self.rsi[-1] < self.buy_th:
            self.buy()
        elif self.position and self.rsi[-1] > self.sell_th:
            self.position.close()


BUILTIN = {
    "sma_cross": {
        "cls": SmaCross,
        "label": "SMA 골든/데드 크로스",
        "params": {"n1": 10, "n2": 30},
    },
    "donchian": {
        "cls": DonchianBreakout,
        "label": "돈치안 채널 돌파",
        "params": {"entry_n": 20, "exit_n": 10},
    },
    "rsi2": {
        "cls": Rsi2,
        "label": "RSI(2) 평균회귀",
        "params": {"rsi_n": 2, "trend_n": 200, "buy_th": 10, "sell_th": 70},
    },
}


def list_strategies():
    return [
        {"id": k, "label": v["label"], "params": v["params"]}
        for k, v in BUILTIN.items()
    ]


def load_custom(code: str):
    """
    사용자 파이썬 코드에서 Strategy 서브클래스를 추출.
    사용 가능한 이름: Strategy, crossover, SMA, pd, np
    ⚠️  임의 코드 실행이므로 신뢰할 수 있는 로컬 환경에서만 사용.
    """
    ns = {
        "Strategy": Strategy, "crossover": crossover,
        "SMA": SMA, "pd": pd, "np": np,
    }
    exec(code, ns)  # noqa: S102 - 개인 로컬 툴 용도
    for v in ns.values():
        if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy:
            return v
    raise ValueError("Strategy 를 상속한 클래스를 코드에서 찾지 못했습니다.")
