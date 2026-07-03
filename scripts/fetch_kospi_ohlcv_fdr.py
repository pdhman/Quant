"""
KOSPI 보통주 OHLCV 데이터 수집기 (FinanceDataReader 버전)
========================================================
pykrx 가 KRX 사이트 변경으로 깨져(빈 응답) 동작하지 않아, 데이터 소스를
FinanceDataReader(FDR) 로 교체한 버전.

유지한 설계:
  ① sleep/재시도    : 모든 FDR 호출을 safe_call()로 감싸 sleep + backoff 재시도
  ② 증분 저장/resume: 종목별로 raw CSV에 append. 중간에 죽어도 이어받기 가능
  ③ 예외 로깅       : 조용히 삼키지 않고 logging 으로 파일 + 콘솔에 기록
  ④ 생존편향 제거   : 현재 상장 KOSPI + (가능하면) 상장폐지 종목까지 유니버스에 포함
  ⑤ 스크리닝 정밀도 : FDR은 종목별 전체 시세를 받으므로, 샘플링이 아니라
                      '연도별 실제 일평균 거래대금(≈종가×거래량)'으로 정확히 랭킹
  ⑥ 최신일자 확장   : END_DATE 기본값 = 오늘

동작 개요:
  STEP1 유니버스 구성 → STEP2 유니버스 전 종목 OHLCV 다운로드(증분/resume)
  → STEP3 연도별 Top250 스크리닝(1회 이상 진입=safe)
  → STEP4 safe 종목만 통합 CSV로 저장

사용법:
    pip install -U finance-datareader pandas
    python fetch_kospi_ohlcv_fdr.py                       # 2016-01-01 ~ 오늘
    python fetch_kospi_ohlcv_fdr.py                       # 재실행 시 자동 이어받기
    RESUME=0 python fetch_kospi_ohlcv_fdr.py              # 처음부터 다시
    START_DATE=20200101 END_DATE=20260101 python fetch_kospi_ohlcv_fdr.py
    INCLUDE_DELISTED=0 python fetch_kospi_ohlcv_fdr.py    # 상폐종목 제외(현재상장만)
"""

import os
import re
import sys
import time
import logging
import warnings
from datetime import datetime

import pandas as pd
import FinanceDataReader as fdr

warnings.filterwarnings("ignore")

# ============================================================
# 설정
# ============================================================
START_DATE = os.environ.get("START_DATE", "20160101")
END_DATE = os.environ.get("END_DATE", datetime.now().strftime("%Y%m%d"))
MARKET = "KOSPI"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_BASE_DIR, "kospi_ohlcv_data")

# raw: 유니버스 전 종목(다운로드/resume 캐시) / output: safe 종목만(최종 결과물)
RAW_CSV = os.path.join(_BASE_DIR, f"krx_ohlcv_raw_{START_DATE}_{END_DATE}.csv")
OUTPUT_CSV = os.path.join(_BASE_DIR, f"krx_ohlcv_{START_DATE}_{END_DATE}.csv")

TOP_N = 250                     # 연평균 거래대금 상위 N위 기준
INCLUDE_DELISTED = os.environ.get("INCLUDE_DELISTED", "1") != "0"

RESUME = os.environ.get("RESUME", "1") != "0"

# sleep / 재시도 (과도한 요청으로 인한 일시 차단 방지)
DL_SLEEP = float(os.environ.get("DL_SLEEP", "0.2"))   # 종목별 다운로드 간 sleep
MAX_RETRY = 3
RETRY_BACKOFF = 2.0

META_PROGRESS = os.path.join(OUTPUT_DIR, "_meta_progress.csv")
META_FAILED = os.path.join(OUTPUT_DIR, "_meta_failed.csv")
LOG_FILE = os.path.join(OUTPUT_DIR, "fetch.log")

# 날짜 문자열(YYYYMMDD) → FDR용(YYYY-MM-DD)
S_DATE = f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}"
E_DATE = f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}"


# ============================================================
# 로깅
# ============================================================
def setup_logger():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logger = logging.getLogger("kospi_fetch")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False  # 중복 출력 방지

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


logger = setup_logger()


# ============================================================
# API 안전 호출 (sleep + 재시도 + 로깅)
# ============================================================
def safe_call(fn, *args, sleep=DL_SLEEP, retries=MAX_RETRY, label="", **kwargs):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            result = fn(*args, **kwargs)
            time.sleep(sleep)
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = max(sleep, 0.5) * (RETRY_BACKOFF ** attempt)
            logger.warning(f"{label} 실패 (시도 {attempt}/{retries}): {e} → {wait:.1f}s 후 재시도")
            time.sleep(wait)
    logger.error(f"{label} 최종 실패: {last_err}")
    return None


# ============================================================
# 유틸리티
# ============================================================
def is_common_stock(code: str) -> bool:
    """보통주 여부 (끝자리 0이면 보통주)"""
    return len(code) == 6 and code[-1] == "0"


def is_spac_or_special(name: str) -> bool:
    patterns = [r"스팩", r"제\d+호", r"리츠$", r"기업인수목적", r"SPAC"]
    return any(re.search(pat, str(name)) for pat in patterns)


def _find_col(df: pd.DataFrame, candidates) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    return None


# ============================================================
# ④ 유니버스 — 현재 상장 KOSPI + (가능하면) 상장폐지 종목
# ============================================================
def build_universe():
    logger.info("=" * 60)
    logger.info("[STEP 1] 종목 유니버스 구성")
    logger.info("=" * 60)

    listing = safe_call(fdr.StockListing, "KOSPI", sleep=0.5, label="StockListing(KOSPI)")
    if listing is None or len(listing) == 0:
        logger.error("  KOSPI 종목 리스트를 가져오지 못했습니다. 종료합니다.")
        return {}

    listing = listing.reset_index()
    code_col = _find_col(listing, ["Code", "Symbol", "종목코드"])
    name_col = _find_col(listing, ["Name", "종목명"])
    if code_col is None or name_col is None:
        logger.error(f"  StockListing 컬럼 인식 실패: {list(listing.columns)}")
        return {}

    universe = {}
    for _, row in listing.iterrows():
        code = str(row[code_col]).zfill(6)
        name = row[name_col]
        if not is_common_stock(code) or is_spac_or_special(name):
            continue
        universe[code] = {"code": code, "name": name, "delisted": False,
                          "delisting_date": ""}

    logger.info(f"  현재 상장 KOSPI 보통주: {len(universe)}개")

    # 상장폐지 종목 추가 (생존편향 제거) — 스키마가 버전마다 달라 best-effort
    if INCLUDE_DELISTED:
        added = add_delisted(universe)
        logger.info(f"  상장폐지 종목 추가: {added}개 (생존편향 보정)")
    else:
        logger.info("  INCLUDE_DELISTED=0 → 상장폐지 종목 제외(현재 상장만)")

    logger.info(f"  최종 유니버스: {len(universe)}개")
    return universe


def add_delisted(universe: dict) -> int:
    """KRX-DELISTING 목록에서 기간 내 상폐된 KOSPI 보통주를 유니버스에 추가."""
    dl = safe_call(fdr.StockListing, "KRX-DELISTING", sleep=0.5,
                   label="StockListing(KRX-DELISTING)")
    if dl is None or len(dl) == 0:
        logger.warning("  상장폐지 목록을 가져오지 못했습니다 → 현재 상장 종목만 사용")
        return 0

    dl = dl.reset_index()
    code_col = _find_col(dl, ["Symbol", "Code", "종목코드"])
    name_col = _find_col(dl, ["Name", "종목명"])
    mkt_col = _find_col(dl, ["Market", "market", "시장구분"])
    del_col = _find_col(dl, ["DelistingDate", "폐지일", "상장폐지일"])

    if code_col is None or name_col is None:
        logger.warning(f"  상폐목록 컬럼 인식 실패({list(dl.columns)}) → 현재 상장만 사용")
        return 0
    if del_col is None or mkt_col is None:
        # 시장/상폐일 정보가 없으면 KOSDAQ 혼입·기간 밖 종목을 거를 수 없어 스킵
        logger.warning("  상폐목록에 시장/상폐일 컬럼이 없어 안전하게 스킵(현재 상장만 사용)")
        return 0

    added = 0
    for _, row in dl.iterrows():
        market = str(row[mkt_col])
        if "KOSPI" not in market.upper() and "유가" not in market:
            continue
        del_date = str(row[del_col]).replace("-", "")[:8]
        if not del_date.isdigit():
            continue
        # 기간과 겹치는 종목만 (상폐일이 시작일 이후여야 데이터가 존재)
        if del_date < START_DATE:
            continue
        code = str(row[code_col]).zfill(6)
        name = row[name_col]
        if not is_common_stock(code) or is_spac_or_special(name):
            continue
        if code in universe:
            continue
        universe[code] = {"code": code, "name": name, "delisted": True,
                          "delisting_date": del_date}
        added += 1
    return added


# ============================================================
# ② 증분 저장 유틸
# ============================================================
def append_csv(path: str, df: pd.DataFrame):
    header = (not os.path.exists(path)) or os.path.getsize(path) == 0
    df.to_csv(path, mode="a", header=header, index=False, encoding="utf-8-sig")


def load_done_codes() -> set:
    if RESUME and os.path.exists(META_PROGRESS):
        try:
            df = pd.read_csv(META_PROGRESS, dtype={"code": str})
            done = set(df.loc[df["status"] == "OK", "code"].str.zfill(6).unique())
            logger.info(f"  이어받기(resume): 이미 받은 {len(done)}종목 스킵")
            return done
        except Exception as e:  # noqa: BLE001
            logger.warning(f"  진행파일 읽기 실패, 처음부터: {e}")
    return set()


def fresh_start_cleanup():
    if RESUME:
        return
    for p in (RAW_CSV, OUTPUT_CSV, META_PROGRESS, META_FAILED):
        if os.path.exists(p):
            os.remove(p)
            logger.info(f"  RESUME=0 → 삭제: {os.path.basename(p)}")


# ============================================================
# STEP 2 — 유니버스 전 종목 OHLCV 다운로드 (증분/resume)
# ============================================================
def download_all(universe: dict):
    logger.info("")
    logger.info("=" * 60)
    logger.info("[STEP 2] 유니버스 전 종목 OHLCV 다운로드 (증분 저장)")
    logger.info("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fresh_start_cleanup()
    done = load_done_codes()

    items = sorted(universe.values(), key=lambda x: x["code"])
    total = len(items)
    ok = fail = skip = 0

    for i, item in enumerate(items):
        code, name = item["code"], item["name"]
        if code in done:
            skip += 1
            continue

        prefix = f"  [{i + 1:4d}/{total}] {code} {name}"
        df = safe_call(fdr.DataReader, code, S_DATE, E_DATE,
                       sleep=DL_SLEEP, label=f"DataReader({code})")

        if df is None:
            logger.error(f"{prefix} ... 실패(재시도 소진)")
            append_csv(META_FAILED, pd.DataFrame([{
                "code": code, "name": name, "error": "retry_exhausted",
                "ts": datetime.now().isoformat(timespec="seconds")}]))
            fail += 1
            continue

        if df is None or df.empty:
            logger.warning(f"{prefix} ... 데이터 없음 → SKIP")
            append_csv(META_PROGRESS, pd.DataFrame([{
                "code": code, "name": name, "delisted": item["delisted"],
                "data_start": "", "data_end": "", "num_rows": 0, "status": "EMPTY"}]))
            continue

        df = df.reset_index()
        date_col = _find_col(df, ["Date", "index", "날짜"]) or df.columns[0]
        rename = {date_col: "date", "Open": "open", "High": "high",
                  "Low": "low", "Close": "close", "Volume": "volume"}
        df = df.rename(columns=rename)
        need = ["date", "open", "high", "low", "close", "volume"]
        if not all(c in df.columns for c in need):
            logger.warning(f"{prefix} ... 컬럼 불일치({list(df.columns)}) → SKIP")
            continue

        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df["code"] = code
        df = df[["date", "code", "open", "high", "low", "close", "volume"]]
        df = df.dropna(subset=["close"])
        if df.empty:
            continue

        append_csv(RAW_CSV, df)
        append_csv(META_PROGRESS, pd.DataFrame([{
            "code": code, "name": name, "delisted": item["delisted"],
            "data_start": df["date"].iloc[0], "data_end": df["date"].iloc[-1],
            "num_rows": len(df), "status": "OK"}]))

        marker = " ☠상폐" if item["delisted"] else ""
        logger.info(f"{prefix} ... OK ({len(df)}일){marker}")
        ok += 1

    logger.info("-" * 60)
    logger.info(f"  다운로드 완료: OK {ok} / 스킵(완료) {skip} / 실패 {fail}")
    logger.info("-" * 60)


# ============================================================
# STEP 3 — 연도별 Top250 스크리닝 (실제 일평균 거래대금 기반)
# ============================================================
def screen_by_trading_value(universe: dict) -> set:
    logger.info("")
    logger.info("=" * 60)
    logger.info("[STEP 3] 연도별 거래대금 Top250 스크리닝")
    logger.info("=" * 60)

    if not os.path.exists(RAW_CSV) or os.path.getsize(RAW_CSV) == 0:
        logger.error("  raw 데이터 없음 → 스크리닝 불가")
        return set()

    df = pd.read_csv(RAW_CSV, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    df["date"] = pd.to_datetime(df["date"])
    df["year"] = df["date"].dt.year
    # 거래대금 ≈ 종가 × 거래량 (FDR DataReader는 거래대금을 따로 주지 않음)
    df["tv"] = df["close"] * df["volume"]

    safe_tickers = set()
    yearly_rows = []
    for year, g in df.groupby("year"):
        avg = g.groupby("code")["tv"].mean().sort_values(ascending=False)
        top = set(avg.head(TOP_N).index)
        safe_tickers |= top
        for rank, (code, val) in enumerate(avg.items(), 1):
            yearly_rows.append({"year": int(year), "rank": rank, "code": code,
                                "name": universe.get(code, {}).get("name", ""),
                                "avg_trading_value": int(val),
                                "is_top250": rank <= TOP_N})
        logger.info(f"  [{year}] 종목 {len(avg)}개 중 Top{TOP_N} 선정, 누적 safe={len(safe_tickers)}")

    if yearly_rows:
        pd.DataFrame(yearly_rows).to_csv(
            os.path.join(OUTPUT_DIR, "_debug_yearly_ranks.csv"),
            index=False, encoding="utf-8-sig")
        logger.info("  연도별 순위 저장: _debug_yearly_ranks.csv")

    logger.info(f"  스크리닝 완료 → safe {len(safe_tickers)}개")
    return safe_tickers


# ============================================================
# STEP 4 — safe 종목만 통합 CSV로 저장
# ============================================================
def finalize(safe_tickers: set):
    logger.info("")
    logger.info("=" * 60)
    logger.info("[STEP 4] safe 종목 통합 CSV 저장")
    logger.info("=" * 60)

    if not safe_tickers:
        logger.warning("  safe 종목 없음 → 저장 생략")
        return

    df = pd.read_csv(RAW_CSV, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    df = df[df["code"].isin(safe_tickers)].copy()
    df = df.rename(columns={"code": "ticker"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = (df.drop_duplicates(subset=["date", "ticker"])
            .sort_values(["date", "ticker"])
            .reset_index(drop=True))
    df = df[["date", "ticker", "open", "high", "low", "close", "volume"]]
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    logger.info(f"  파일: {OUTPUT_CSV}")
    logger.info(f"  {len(df):,}행, {df['ticker'].nunique()}종목")
    logger.info(f"  기간: {df['date'].min()} ~ {df['date'].max()}")


# ============================================================
# 메인
# ============================================================
def main():
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  KOSPI 보통주 OHLCV 수집기 (FinanceDataReader)           ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")
    logger.info(f"  기간: {START_DATE} ~ {END_DATE}  |  상폐포함: {INCLUDE_DELISTED}")
    logger.info(f"  출력: {OUTPUT_CSV}")
    logger.info(f"  RESUME = {RESUME}")

    t0 = time.time()
    try:
        universe = build_universe()
        if not universe:
            logger.error("유니버스가 비어 종료합니다.")
            return
        download_all(universe)
        safe = screen_by_trading_value(universe)
        finalize(safe)
    except KeyboardInterrupt:
        logger.warning("사용자 중단(Ctrl+C). 저장된 데이터는 보존, 재실행 시 이어받기.")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"예기치 못한 오류로 중단: {e}. 저장분 보존, 재실행 시 이어받기.")

    logger.info(f"  총 소요: {(time.time() - t0) / 60:.1f}분 / 로그: {LOG_FILE}")
    logger.info("  종료")


if __name__ == "__main__":
    main()
