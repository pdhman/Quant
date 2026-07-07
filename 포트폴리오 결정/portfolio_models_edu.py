"""
포트폴리오 결정이론 교육용 최소 예제 스크립트
==================================================

원본 GUI 프로그램(portfolio_optimizer.py)에서 핵심 최적화 로직만 뽑아
강의/실습용으로 재구성했습니다. PyQt5, matplotlib GUI 등 의존성 없이
순수 numpy/pandas만으로 동작하며, 각 모델을 독립 함수로 분리해
수식과 코드를 1:1로 대응시켜 읽을 수 있게 했습니다.

구현 모델 (모두 기대수익률을 쓰지 않는 '위험 기반' 배분):
  1) Equal-Weight  (동일가중)   : w_i = 1/n
  2) GMV           (최소분산)   : min  wᵀΣw
  3) MDP           (최대분산투자): max  (wᵀσ)/sqrt(wᵀΣw)
  4) Risk Parity   (위험균등)   : min  Σ(RC_i - 1/n)²

사용법:
  python portfolio_models_edu.py --demo          # 내장 3종목 예제 실행
  python portfolio_models_edu.py --excel FILE     # 엑셀 가격 데이터로 실행
  python portfolio_models_edu.py --corr 0.0       # 상관관계를 바꿔가며 MDP 관찰

의존성:  numpy, pandas  (엑셀 읽을 때만 openpyxl 필요)
"""

import argparse
import numpy as np

try:
    import pandas as pd
except ImportError:  # pandas 없이도 --demo는 동작하도록
    pd = None


# =============================================================================
# 공통 유틸: 제약조건 투영 + 최적화 엔진
# =============================================================================
def project_weights(weights, lower, upper):
    """비중을 [lower, upper]로 clip 한 뒤 합계=1로 정규화 (원본 project_weights와 동일).

    이것이 '투영(projection)'이다: 경사하강으로 이동한 점을
    제약조건을 만족하는 가장 가까운(근사) 점으로 되돌린다.
    """
    weights = np.clip(weights, lower, upper)
    weights = weights / np.sum(weights)
    return weights


def auto_constraints(n):
    """종목 수 n에 따른 동적 제약조건 (원본 36~38行과 동일).

    lower = max(1%, 균등배분의 50%),  upper = min(40%, 균등배분의 300%)
    """
    lower = max(0.01, 0.5 / n)
    upper = min(0.40, 3.0 / n)
    return lower, upper


def projected_gradient_descent(objective, gradient, n, lower, upper,
                               n_restarts=30, learning_rate=0.01,
                               max_iter=1000, tol=1e-6, seed=None):
    """투영 경사하강법 + multi-start (원본 grg_optimize의 교육용 일반화).

    목적함수를 '최소화'한다. 최대화 문제는 -objective 를 넘기면 된다.
    비볼록 목적함수의 지역해를 피하려고 랜덤 초기값에서 여러 번 재시작한다.
    """
    rng = np.random.default_rng(seed)
    best_w, best_obj = None, float("inf")

    for _ in range(n_restarts):
        # --- 랜덤 초기값 (제약 범위 안에서 뽑고 정규화) ---
        w = rng.uniform(lower, upper, n)
        w = w / np.sum(w)

        prev_obj = float("inf")
        lr = learning_rate
        for _ in range(max_iter):
            grad = gradient(w)
            w = w - lr * grad             # 1. 하강 스텝
            w = project_weights(w, lower, upper)  # 2. 제약으로 투영
            curr_obj = objective(w)
            if abs(curr_obj - prev_obj) < tol:    # 3. 수렴 판정
                break
            prev_obj = curr_obj
            lr *= 0.999                   # 4. 학습률 점진 감소

        if curr_obj < best_obj:
            best_obj = curr_obj
            best_w = w

    return best_w, best_obj


# =============================================================================
# 모델 ① Equal-Weight (동일가중)
# =============================================================================
def equal_weight(cov):
    """w_i = 1/n. 위험조차 보지 않는 벤치마크."""
    n = len(cov)
    return np.ones(n) / n


# =============================================================================
# 모델 ② GMV (Global Minimum Variance, 최소분산)
#   min  wᵀΣw        gradient = 2Σw
# =============================================================================
def gmv_weights(cov, lower=None, upper=None, seed=0):
    n = len(cov)
    Sigma = np.asarray(cov, dtype=float)
    if lower is None or upper is None:
        lower, upper = auto_constraints(n)

    def objective(w):
        return float(w.T @ Sigma @ w)          # 포트폴리오 분산

    def gradient(w):
        return 2 * (Sigma @ w)                  # ∇(wᵀΣw) = 2Σw

    w, _ = projected_gradient_descent(objective, gradient, n, lower, upper, seed=seed)
    return w


# =============================================================================
# 모델 ③ MDP (Most Diversified Portfolio, 최대분산투자)
#   max  DR(w) = (wᵀσ) / sqrt(wᵀΣw)   ==>   min  -DR(w)
# =============================================================================
def mdp_weights(cov, lower=None, upper=None, seed=0):
    n = len(cov)
    Sigma = np.asarray(cov, dtype=float)
    vols = np.sqrt(np.diag(Sigma))              # 개별 종목 변동성 σ_i
    if lower is None or upper is None:
        lower, upper = auto_constraints(n)

    def objective(w):
        port_vol = np.sqrt(w.T @ Sigma @ w)
        if port_vol < 1e-10:
            return float("inf")
        return -float(w @ vols) / port_vol       # -DR (최소화하려고 음수)

    def gradient(w):
        port_vol = np.sqrt(w.T @ Sigma @ w)
        if port_vol < 1e-10:
            return np.zeros(n)
        weighted_vols = w @ vols
        grad_port_vol = (Sigma @ w) / port_vol
        # d(-DR)/dw = -(σ·port_vol - (wᵀσ)·∂port_vol) / port_vol²
        return -(vols * port_vol - weighted_vols * grad_port_vol) / (port_vol ** 2)

    w, _ = projected_gradient_descent(objective, gradient, n, lower, upper, seed=seed)
    return w


def diversification_ratio(w, cov):
    """분산비율 DR = (가중 개별위험 합) / (실제 포트폴리오 위험). 클수록 분산효과 큼."""
    Sigma = np.asarray(cov, dtype=float)
    vols = np.sqrt(np.diag(Sigma))
    return float(w @ vols) / float(np.sqrt(w.T @ Sigma @ w))


# =============================================================================
# 모델 ④ Risk Parity (위험균등)
#   각 종목의 위험기여도 RC_i 를 1/n 으로 균등화
#   RC_i = w_i (Σw)_i / σ_p,   min Σ(RC_i - 1/n)²
# =============================================================================
def risk_contributions(w, cov):
    """정규화된 위험기여도 RC_i / σ_p (합계=1)."""
    Sigma = np.asarray(cov, dtype=float)
    port_vol = np.sqrt(w.T @ Sigma @ w)
    if port_vol < 1e-10:
        return np.zeros(len(w))
    marginal = Sigma @ w                        # 한계기여 Σw
    return np.multiply(marginal, w) / port_vol   # w_i (Σw)_i / σ_p


def risk_parity_weights(cov, lower=None, upper=None, seed=0):
    n = len(cov)
    Sigma = np.asarray(cov, dtype=float)
    if lower is None or upper is None:
        lower, upper = auto_constraints(n)
    target = 1.0 / n

    def objective(w):
        rc = risk_contributions(w, Sigma)
        pct = rc / rc.sum()                     # 퍼센트 위험기여도 (합계=1)
        return float(np.sum((pct - target) ** 2))  # 목표: 모든 종목이 1/n

    def gradient(w):
        # RC의 해석적 미분이 복잡하므로 수치미분(중앙차분) 사용 (원본과 동일한 선택)
        eps = 1e-8
        grad = np.zeros(n)
        for i in range(n):
            h = np.zeros(n)
            h[i] = eps
            grad[i] = (objective(w + h) - objective(w - h)) / (2 * eps)
        return grad

    w, _ = projected_gradient_descent(objective, gradient, n, lower, upper, seed=seed)
    return w


# =============================================================================
# 성과지표 (원본 update_results 736~749行과 동일 공식)
# =============================================================================
def performance_metrics(weights, returns_df, periods_per_year=252):
    """returns_df: (기간 x 종목) 일간수익률 DataFrame. 연환산 지표 dict 반환."""
    port_returns = returns_df.dot(weights)
    cum_ret = (1 + port_returns).cumprod()
    total_ret = cum_ret.iloc[-1] - 1
    n_days = len(returns_df)
    ann_ret = (1 + total_ret) ** (periods_per_year / n_days) - 1
    ann_vol = port_returns.std() * np.sqrt(periods_per_year)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0   # 무위험이자율=0 가정
    mdd = ((cum_ret - cum_ret.cummax()) / cum_ret.cummax()).min()
    return {
        "연환산수익률": ann_ret,
        "연환산변동성": ann_vol,
        "샤프비율": sharpe,
        "최대낙폭(MDD)": mdd,
    }


# =============================================================================
# 엑셀 로더 (원본의 세로형/가로형 자동감지를 축약)
# =============================================================================
def load_excel_returns(path):
    """엑셀에서 (returns_df, tickers) 반환. 세로형/가로형 자동 감지."""
    if pd is None:
        raise RuntimeError("엑셀 로드에는 pandas가 필요합니다.")
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    lower_cols = [c.lower() for c in df.columns]

    if {"date", "ticker", "price"}.issubset(set(lower_cols)):
        # 세로형 (Long): Date/Ticker/Price
        rename = {c: c.lower() for c in df.columns if c.lower() in ("date", "ticker", "price")}
        df = df.rename(columns=rename)
        df["date"] = pd.to_datetime(df["date"])
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df = df.dropna(subset=["date", "ticker", "price"])
        pivot = df.pivot_table(index="date", columns="ticker", values="price", aggfunc="last")
    else:
        # 가로형 (Wide): 첫 컬럼이 날짜, 나머지가 종목 가격
        date_col = df.columns[0]
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)
        pivot = df.select_dtypes(include=[np.number])

    pivot = pivot.ffill().bfill().dropna()
    returns = pivot.pct_change().dropna()
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
    return returns, list(returns.columns)


# =============================================================================
# 실행부: 데모 / 상관관계 실험 / 엑셀
# =============================================================================
def _print_weights(name, weights, tickers, cov):
    print(f"\n[{name}]  DR={diversification_ratio(weights, cov):.3f}")
    for t, w in zip(tickers, weights):
        bar = "#" * int(w * 40)
        print(f"  {t:>6s}: {w:6.2%}  {bar}")
    rc = risk_contributions(weights, cov)
    pct = rc / rc.sum()   # 퍼센트 위험기여도 (합계=100%)
    print("  위험기여도(%):", "  ".join(f"{t}={p:.1%}" for t, p in zip(tickers, pct)))


def run_all_models(cov, tickers, lower=None, upper=None):
    print("=" * 60)
    print("공분산 행렬:")
    print(np.round(cov, 6))
    if lower is None or upper is None:
        lower, upper = auto_constraints(len(cov))
    print(f"제약조건: {lower:.1%} ~ {upper:.1%}")

    _print_weights("Equal-Weight", equal_weight(cov), tickers, cov)
    _print_weights("GMV (최소분산)", gmv_weights(cov, lower, upper), tickers, cov)
    _print_weights("MDP (최대분산투자)", mdp_weights(cov, lower, upper), tickers, cov)
    _print_weights("Risk Parity (위험균등)", risk_parity_weights(cov, lower, upper), tickers, cov)


def make_cov(vols, corr_matrix):
    """변동성 벡터 + 상관행렬 -> 공분산 행렬.  Σ_ij = σ_i σ_j ρ_ij"""
    vols = np.asarray(vols, dtype=float)
    D = np.diag(vols)
    return D @ np.asarray(corr_matrix, dtype=float) @ D


def demo():
    """내장 3종목 예제: 변동성과 상관관계가 다른 자산 A/B/C."""
    tickers = ["A", "B", "C"]
    vols = [0.10, 0.20, 0.15]           # A는 저변동, B는 고변동
    corr = [
        [1.0, 0.2, 0.5],
        [0.2, 1.0, 0.3],
        [0.5, 0.3, 1.0],
    ]
    cov = make_cov(vols, corr)
    # 데모에서는 제약을 느슨하게(5%~60%) 주어 각 모델의 성향이 뚜렷하게 드러나도록 함
    run_all_models(cov, tickers, lower=0.05, upper=0.60)
    print("\n관찰 포인트:")
    print(" - GMV: 저변동성 A에 비중이 쏠리는 경향")
    print(" - MDP: 상관이 낮은 조합을 선호")
    print(" - Risk Parity: 위험기여도(%)가 모두 1/3 ≈ 33%에 수렴하는지 확인")
    print(" - 고변동성 B는 Risk Parity에서 비중이 작아진다(위험을 균등하게 나누려고)")


def corr_experiment(rho):
    """실습 2: A-B 상관계수를 바꿔가며 MDP 비중 변화 관찰."""
    tickers = ["A", "B"]
    vols = [0.15, 0.15]                 # 변동성 동일 -> 순수하게 상관효과만 관찰
    corr = [[1.0, rho], [rho, 1.0]]
    cov = make_cov(vols, corr)
    w_mdp = mdp_weights(cov)
    w_gmv = gmv_weights(cov)
    print(f"\nρ(A,B) = {rho:+.2f}")
    print(f"  MDP 비중: A={w_mdp[0]:.2%}, B={w_mdp[1]:.2%}  (DR={diversification_ratio(w_mdp, cov):.3f})")
    print(f"  GMV 비중: A={w_gmv[0]:.2%}, B={w_gmv[1]:.2%}")
    print("  → 변동성이 같으면 MDP/GMV 모두 50:50에 수렴. 상관이 낮을수록 DR이 커진다.")


def run_excel(path):
    returns, tickers = load_excel_returns(path)
    cov = returns.cov().values
    print(f"로드 완료: {len(returns)}일, {len(tickers)}종목")
    run_all_models(cov, tickers)

    print("\n" + "=" * 60)
    print("성과지표 비교")
    print("=" * 60)
    models = {
        "Equal-Weight": equal_weight(cov),
        "GMV": gmv_weights(cov),
        "MDP": mdp_weights(cov),
        "Risk Parity": risk_parity_weights(cov),
    }
    for name, w in models.items():
        m = performance_metrics(w, returns)
        print(f"\n[{name}]")
        for k, v in m.items():
            print(f"  {k:>14s}: {v:8.2%}" if "비율" not in k else f"  {k:>14s}: {v:8.2f}")


def main():
    parser = argparse.ArgumentParser(description="포트폴리오 결정이론 교육용 예제")
    parser.add_argument("--demo", action="store_true", help="내장 3종목 예제 실행")
    parser.add_argument("--excel", metavar="FILE", help="엑셀 가격 데이터 파일 경로")
    parser.add_argument("--corr", type=float, metavar="RHO",
                        help="A-B 상관계수를 지정해 MDP 비중 변화 관찰 (예: --corr 0.0)")
    args = parser.parse_args()

    if args.excel:
        run_excel(args.excel)
    elif args.corr is not None:
        corr_experiment(args.corr)
    else:
        # 기본: 데모 실행
        demo()


if __name__ == "__main__":
    main()
