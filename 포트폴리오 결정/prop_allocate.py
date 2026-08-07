"""
prop 포트폴리오 비중 결정 — 위험 기반 4대 모델

사용법:
  python prop_allocate.py 005930 000660 035420 ...
  python prop_allocate.py --years 3 005930 000660 ...
  python prop_allocate.py --min 5 --max 30 005930 ...
"""
import argparse
import sys
import numpy as np
import pandas as pd

PRICE_CSV = "data/krx/krx_ohlcv_20160101_20260101.csv"
NAME_CSV = "data/퀀트데이터_260102.csv"


# ---------- 최적화 엔진 ----------
def project_weights(w, lo, hi):
    w = np.clip(w, lo, hi)
    return w / w.sum()


def pgd(obj, grad, n, lo, hi, restarts=40, lr0=0.01, max_iter=2000, tol=1e-9, seed=0):
    rng = np.random.default_rng(seed)
    best_w, best_o = None, float("inf")
    for _ in range(restarts):
        w = rng.uniform(lo, hi, n)
        w = w / w.sum()
        prev, lr = float("inf"), lr0
        cur = obj(w)
        for _ in range(max_iter):
            w = project_weights(w - lr * grad(w), lo, hi)
            cur = obj(w)
            if abs(cur - prev) < tol:
                break
            prev, lr = cur, lr * 0.999
        if cur < best_o:
            best_o, best_w = cur, w
    return best_w


# ---------- 4대 모델 ----------
def equal_weight(cov):
    n = len(cov)
    return np.ones(n) / n


def gmv(cov, lo, hi):
    n = len(cov)
    S = np.asarray(cov, float)
    return pgd(lambda w: float(w @ S @ w), lambda w: 2 * (S @ w), n, lo, hi)


def mdp(cov, lo, hi):
    n = len(cov)
    S = np.asarray(cov, float)
    v = np.sqrt(np.diag(S))

    def obj(w):
        pv = np.sqrt(w @ S @ w)
        return float("inf") if pv < 1e-12 else -float(w @ v) / pv

    def grad(w):
        pv = np.sqrt(w @ S @ w)
        if pv < 1e-12:
            return np.zeros(n)
        return -(v * pv - (w @ v) * ((S @ w) / pv)) / pv**2

    return pgd(obj, grad, n, lo, hi)


def risk_contrib(w, cov):
    S = np.asarray(cov, float)
    pv = np.sqrt(w @ S @ w)
    return np.zeros(len(w)) if pv < 1e-12 else np.multiply(S @ w, w) / pv


def risk_parity(cov, lo, hi):
    n = len(cov)
    S = np.asarray(cov, float)
    t = 1.0 / n

    def obj(w):
        rc = risk_contrib(w, S)
        s = rc.sum()
        if s <= 0:
            return float("inf")
        return float(np.sum((rc / s - t) ** 2))

    def grad(w):
        eps, g = 1e-7, np.zeros(n)
        for i in range(n):
            h = np.zeros(n)
            h[i] = eps
            g[i] = (obj(w + h) - obj(w - h)) / (2 * eps)
        return g

    return pgd(obj, grad, n, lo, hi)


def inverse_vol(cov):
    """참고용: 역변동성 가중 (Naive Risk Parity)"""
    v = np.sqrt(np.diag(np.asarray(cov, float)))
    w = 1.0 / v
    return w / w.sum()


# ---------- 성과지표 ----------
def metrics(w, rets, ppy=252):
    pr = rets.dot(w)
    cum = (1 + pr).cumprod()
    total = cum.iloc[-1] - 1
    ann_r = (1 + total) ** (ppy / len(rets)) - 1
    ann_v = pr.std() * np.sqrt(ppy)
    return {
        "연수익률": ann_r,
        "연변동성": ann_v,
        "샤프": ann_r / ann_v if ann_v > 0 else 0.0,
        "MDD": ((cum - cum.cummax()) / cum.cummax()).min(),
    }


def div_ratio(w, cov):
    S = np.asarray(cov, float)
    return float(w @ np.sqrt(np.diag(S))) / float(np.sqrt(w @ S @ w))


# ---------- 데이터 ----------
def load_prices(tickers, years):
    df = pd.read_csv(PRICE_CSV, dtype={"ticker": str}, usecols=["date", "ticker", "close"])
    df = df[df.ticker.isin(tickers)]
    missing = set(tickers) - set(df.ticker.unique())
    if missing:
        print(f"⚠ 데이터에 없는 종목: {sorted(missing)}", file=sys.stderr)
    px = df.pivot(index="date", columns="ticker", values="close").sort_index()
    px.index = pd.to_datetime(px.index)
    if years:
        px = px[px.index >= px.index.max() - pd.DateOffset(years=years)]
    px = px.dropna(axis=1, how="all").ffill().dropna()
    return px


def load_names():
    try:
        nm = pd.read_csv(NAME_CSV, usecols=["코드", "회사명"], encoding="utf-8-sig")
        nm["코드"] = nm["코드"].astype(str).str.lstrip("A")
        return dict(zip(nm["코드"], nm["회사명"]))
    except Exception:
        return {}


# ---------- 메인 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+", help="종목코드 (예: 005930 000660)")
    ap.add_argument("--years", type=int, default=1, help="분석 기간(년), 0=전체 (기본 1)")
    ap.add_argument("--min", type=float, default=None, help="최소비중 %% (기본 자동)")
    ap.add_argument("--max", type=float, default=None, help="최대비중 %% (기본 자동)")
    a = ap.parse_args()

    tickers = [t.strip().lstrip("A").zfill(6) for t in a.tickers]
    px = load_prices(tickers, a.years)
    rets = px.pct_change().dropna().replace([np.inf, -np.inf], np.nan).dropna()
    cols = list(rets.columns)
    n = len(cols)
    if n < 2:
        sys.exit("종목이 2개 미만입니다.")

    lo = a.min / 100 if a.min is not None else max(0.01, 0.5 / n)
    hi = a.max / 100 if a.max is not None else min(0.40, 3.0 / n)
    if lo * n > 1 or hi * n < 1:
        sys.exit(f"제약 불가능: {n}종목 × [{lo:.1%}, {hi:.1%}]")

    cov = rets.cov().values
    names = load_names()
    labels = [f"{t} {names.get(t, '')}".strip() for t in cols]

    print(f"\n{'='*72}")
    print(f"  기간: {px.index[0]:%Y-%m-%d} ~ {px.index[-1]:%Y-%m-%d}  ({len(rets)}거래일)")
    print(f"  종목: {n}개   비중제약: {lo:.1%} ~ {hi:.1%}")
    print("=" * 72)

    models = {
        "Equal-Weight": equal_weight(cov),
        "Inverse-Vol": inverse_vol(cov),
        "GMV": gmv(cov, lo, hi),
        "MDP": mdp(cov, lo, hi),
        "Risk Parity": risk_parity(cov, lo, hi),
    }

    # 비중 표
    wdf = pd.DataFrame(models, index=labels)
    print("\n■ 비중 (%)\n")
    print((wdf * 100).round(2).to_string())

    # 위험기여도
    print("\n■ 위험기여도 (%)\n")
    rc = {k: (lambda r: r / r.sum())(risk_contrib(v, cov)) for k, v in models.items()}
    print((pd.DataFrame(rc, index=labels) * 100).round(2).to_string())

    # 성과
    print("\n■ 성과지표 (인샘플)\n")
    perf = pd.DataFrame({k: metrics(v, rets) for k, v in models.items()}).T
    perf["DR"] = [div_ratio(v, cov) for v in models.values()]
    fmt = perf.copy()
    for c in ["연수익률", "연변동성", "MDD"]:
        fmt[c] = (perf[c] * 100).round(2).astype(str) + "%"
    fmt["샤프"] = perf["샤프"].round(2)
    fmt["DR"] = perf["DR"].round(3)
    print(fmt.to_string())

    # 개별 종목 통계
    print("\n■ 개별 종목 (연환산)\n")
    stat = pd.DataFrame({
        "변동성": rets.std() * np.sqrt(252) * 100,
        "수익률": ((1 + rets).prod() ** (252 / len(rets)) - 1) * 100,
    })
    stat.index = labels
    print(stat.round(2).to_string())

    print("\n■ 상관계수\n")
    corr = rets.corr()
    corr.index = corr.columns = [c[:6] for c in cols]
    print(corr.round(2).to_string())
    print()


if __name__ == "__main__":
    main()
