"""
Prop 포트폴리오 비중 결정 — 위험 기반 모델 비교
보유: SK하이닉스, 삼성전자, 대원전선, KODEX200(KS11 프록시), 달바글로벌
"""
import numpy as np
import pandas as pd

from prop_allocate import (
    equal_weight, inverse_vol, gmv, mdp, risk_parity,
    risk_contrib, metrics, div_ratio,
)

PRICE = "data/krx/krx_ohlcv_20160101_20260101.csv"
INDEX = "data/krx/krx_index_KS11_20100101_20261231.csv"

HOLDINGS = {           # 종목명: (코드, 평가금액원)
    "SK하이닉스":   ("000660", 40_365_000),
    "삼성전자":     ("005930", 40_107_000),
    "대원전선":     ("006340",  4_752_000),
    "KODEX200":   ("KS11",   39_560_000),
    "달바글로벌":   ("483650",  7_410_000),
}
CASH = 71_224_905
TOTAL = 203_418_905


def build_prices():
    df = pd.read_csv(PRICE, dtype={"ticker": str}, usecols=["date", "ticker", "close"])
    codes = [c for c, _ in HOLDINGS.values() if c != "KS11"]
    px = df[df.ticker.isin(codes)].pivot(index="date", columns="ticker", values="close")
    idx = pd.read_csv(INDEX).set_index("date")["close"].rename("KS11")
    px = px.join(idx, how="outer").sort_index()
    px.index = pd.to_datetime(px.index)
    rename = {code: name for name, (code, _) in HOLDINGS.items()}
    return px.rename(columns=rename)[list(HOLDINGS)]


def run(px, label, lo=None, hi=None):
    px = px.dropna()
    rets = px.pct_change().dropna().replace([np.inf, -np.inf], np.nan).dropna()
    n = rets.shape[1]
    lo = lo if lo is not None else max(0.01, 0.5 / n)
    hi = hi if hi is not None else min(0.40, 3.0 / n)
    cov = rets.cov().values
    names = list(rets.columns)

    print(f"\n{'='*78}")
    print(f"  {label}")
    print(f"  {px.index[0]:%Y-%m-%d} ~ {px.index[-1]:%Y-%m-%d}   {len(rets)}거래일   "
          f"{n}종목   제약 {lo:.1%}~{hi:.1%}")
    print("=" * 78)

    models = {
        "현재(실제)": None,
        "Equal-Weight": equal_weight(cov),
        "Inverse-Vol": inverse_vol(cov),
        "GMV": gmv(cov, lo, hi),
        "MDP": mdp(cov, lo, hi),
        "Risk Parity": risk_parity(cov, lo, hi),
    }
    # 현재 실제 비중 (위험자산 내)
    amts = np.array([HOLDINGS[k][1] for k in names], float)
    models["현재(실제)"] = amts / amts.sum()

    print("\n■ 위험자산 내 비중 (%)\n")
    print((pd.DataFrame(models, index=names) * 100).round(2).to_string())

    print("\n■ 위험기여도 (%)\n")
    rc = {k: (lambda r: r / r.sum())(risk_contrib(v, cov)) for k, v in models.items()}
    print((pd.DataFrame(rc, index=names) * 100).round(2).to_string())

    print("\n■ 성과·위험 (인샘플)\n")
    perf = pd.DataFrame({k: metrics(v, rets) for k, v in models.items()}).T
    perf["DR"] = [div_ratio(v, cov) for v in models.values()]
    out = pd.DataFrame({
        "연수익률": (perf["연수익률"] * 100).round(1).astype(str) + "%",
        "연변동성": (perf["연변동성"] * 100).round(1).astype(str) + "%",
        "샤프": perf["샤프"].round(2),
        "MDD": (perf["MDD"] * 100).round(1).astype(str) + "%",
        "DR": perf["DR"].round(3),
    })
    print(out.to_string())

    print("\n■ 개별 종목 (연환산)\n")
    stat = pd.DataFrame({
        "변동성": (rets.std() * np.sqrt(252) * 100).round(1),
        "수익률": (((1 + rets).prod() ** (252 / len(rets)) - 1) * 100).round(1),
    })
    print(stat.to_string())

    print("\n■ 상관계수\n")
    print(rets.corr().round(2).to_string())
    return models, rets


def money_table(models, names, keep_cash=True):
    invest = TOTAL - CASH if keep_cash else TOTAL
    print(f"\n\n{'='*78}")
    print(f"  금액 환산 — {'현금 35% 유지' if keep_cash else '전액 투자'} "
          f"(위험자산 {invest:,.0f}원)")
    print("=" * 78 + "\n")
    rows = {}
    for k, w in models.items():
        rows[k] = pd.Series((w * invest).round(-4), index=names)
    df = pd.DataFrame(rows)
    print(df.map(lambda v: f"{v/1e4:,.0f}만").to_string())
    print("\n■ 현재 대비 조정액 (+매수 / -매도)\n")
    cur = df["현재(실제)"]
    delta = df.drop(columns=["현재(실제)"]).sub(cur, axis=0)
    print(delta.map(lambda v: f"{v/1e4:+,.0f}만").to_string())


if __name__ == "__main__":
    px = build_prices()

    # 1) 전 종목 (달바글로벌 상장 이후 공통구간)
    m_all, r_all = run(px, "① 전 보유종목 (달바글로벌 포함 → 공통구간 제약)")
    money_table(m_all, list(r_all.columns), keep_cash=True)

    # 2) 달바글로벌 제외, 최근 1년
    px4 = px.drop(columns=["달바글로벌"])
    px4_1y = px4[px4.index >= px4.dropna().index.max() - pd.DateOffset(years=1)]
    run(px4_1y, "② 달바글로벌 제외 · 최근 1년 (공분산 안정성 검증)")

    # 3) 달바글로벌 제외, 최근 3년
    px4_3y = px4[px4.index >= px4.dropna().index.max() - pd.DateOffset(years=3)]
    run(px4_3y, "③ 달바글로벌 제외 · 최근 3년 (장기 위험구조)")
