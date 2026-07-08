"""
데이터 계층: 종목 검색 + OHLCV 조회
====================================
- 종목 검색: FinanceDataReader 의 KRX 상장 목록을 메모리 캐시
- OHLCV    : 요청 시 FDR 에서 받아오고 SQLite 에 캐시 (다음 요청은 캐시 우선)

기존 scripts/fetch_kospi_ohlcv_fdr.py 로 대량 CSV 를 미리 받아뒀다면
load_from_csv() 로 캐시를 채워 넣을 수도 있다(선택).
"""
import os
import sqlite3
import datetime as dt

import pandas as pd
import FinanceDataReader as fdr

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_DB = os.path.join(BASE, "cache.db")

_listing = None  # 상장 목록 메모리 캐시


# ----------------------------------------------------------------------
# 종목 목록 / 검색
# ----------------------------------------------------------------------
def _load_listing() -> pd.DataFrame:
    """KRX 전체 상장 목록. 컬럼명이 FDR 버전마다 달라 방어적으로 정규화."""
    global _listing
    if _listing is not None:
        return _listing

    df = fdr.StockListing("KRX")
    # code 컬럼 찾기
    code_col = next((c for c in ("Code", "Symbol", "종목코드") if c in df.columns), None)
    name_col = next((c for c in ("Name", "종목명") if c in df.columns), None)
    mkt_col = next((c for c in ("Market", "시장구분") if c in df.columns), None)
    if code_col is None or name_col is None:
        raise RuntimeError(f"상장목록 컬럼 인식 실패: {list(df.columns)}")

    out = pd.DataFrame({
        "code": df[code_col].astype(str).str.zfill(6),
        "name": df[name_col].astype(str),
        "market": df[mkt_col].astype(str) if mkt_col else "",
    })
    out = out[out["code"].str.match(r"^\d{6}$")].reset_index(drop=True)
    _listing = out
    return _listing


def search(q: str, limit: int = 20):
    """종목명 부분일치 또는 코드 접두 일치."""
    q = (q or "").strip()
    if not q:
        return []
    df = _load_listing()
    if q.isdigit():
        m = df[df["code"].str.startswith(q)]
    else:
        m = df[df["name"].str.contains(q, case=False, na=False)]
    return m.head(limit).to_dict("records")


def resolve_name(code: str) -> str:
    df = _load_listing()
    hit = df[df["code"] == str(code).zfill(6)]
    return hit.iloc[0]["name"] if len(hit) else str(code)


# ----------------------------------------------------------------------
# OHLCV (SQLite 캐시)
# ----------------------------------------------------------------------
def _db():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ohlcv(
             code TEXT, date TEXT,
             open REAL, high REAL, low REAL, close REAL, volume REAL,
             PRIMARY KEY(code, date))"""
    )
    return conn


def _read_cache(code, start, end) -> pd.DataFrame:
    conn = _db()
    df = pd.read_sql_query(
        "SELECT date,open,high,low,close,volume FROM ohlcv "
        "WHERE code=? AND date>=? AND date<=? ORDER BY date",
        conn, params=(code, start, end),
    )
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def _write_cache(code, df: pd.DataFrame):
    if df.empty:
        return
    rows = [
        (code, idx.strftime("%Y-%m-%d"),
         float(r.Open), float(r.High), float(r.Low), float(r.Close),
         float(r.Volume) if not pd.isna(r.Volume) else 0.0)
        for idx, r in df.iterrows()
    ]
    conn = _db()
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


def get_ohlcv(code: str, start: str = "2018-01-01", end: str | None = None) -> pd.DataFrame:
    """
    반환: DatetimeIndex + [Open, High, Low, Close, Volume] (backtesting.py 호환)
    FDR 로 받아 캐시. 네트워크 실패 시 캐시로 폴백.
    """
    code = str(code).zfill(6)
    if end is None:
        end = dt.date.today().strftime("%Y-%m-%d")

    try:
        raw = fdr.DataReader(code, start, end)
        if not raw.empty:
            raw = raw.rename(columns=str.title)  # open->Open 등 표준화
            raw = raw[["Open", "High", "Low", "Close", "Volume"]].dropna(
                subset=["Open", "High", "Low", "Close"]
            )
            _write_cache(code, raw)
            return raw
    except Exception as e:  # noqa: BLE001 - 캐시 폴백
        print(f"[data] FDR 실패({code}): {e} → 캐시 사용")

    cached = _read_cache(code, start, end)
    if cached.empty:
        raise RuntimeError(f"데이터를 가져올 수 없습니다: {code}")
    return cached.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]]


def ohlcv_to_candles(df: pd.DataFrame):
    """Lightweight Charts 캔들 포맷으로 변환."""
    return [
        {
            "time": idx.strftime("%Y-%m-%d"),
            "open": float(r.Open), "high": float(r.High),
            "low": float(r.Low), "close": float(r.Close),
        }
        for idx, r in df.iterrows()
    ]
