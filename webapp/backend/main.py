"""
백테스트 툴 백엔드 (FastAPI)
=============================
실행:
    cd webapp/backend
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

엔드포인트:
    GET  /api/search?q=삼성
    GET  /api/ohlcv?code=005930&start=2018-01-01
    GET  /api/strategies
    POST /api/backtest
프론트엔드(../frontend/index.html) 는 / 에서 서빙.
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import data
import strategies
from engine import run_backtest

BASE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(BASE, "..", "frontend")

app = FastAPI(title="My Backtest Tool")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_methods=["*"], allow_headers=["*"],
)


@app.get("/api/search")
def api_search(q: str = ""):
    try:
        return {"results": data.search(q)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"검색 실패: {e}")


@app.get("/api/ohlcv")
def api_ohlcv(code: str, start: str = "2018-01-01", end: str | None = None):
    try:
        df = data.get_ohlcv(code, start, end)
        return {
            "code": code,
            "name": data.resolve_name(code),
            "candles": data.ohlcv_to_candles(df),
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"OHLCV 실패: {e}")


@app.get("/api/strategies")
def api_strategies():
    return {"strategies": strategies.list_strategies()}


class BacktestReq(BaseModel):
    code: str
    start: str = "2018-01-01"
    end: str | None = None
    strategy: str | None = None      # 내장 전략 id
    params: dict = {}
    custom_code: str | None = None   # 직접 입력한 파이썬 전략
    cash: float = 10_000_000
    commission: float = 0.0005


@app.post("/api/backtest")
def api_backtest(req: BacktestReq):
    try:
        df = data.get_ohlcv(req.code, req.start, req.end)

        if req.custom_code and req.custom_code.strip():
            strat_cls = strategies.load_custom(req.custom_code)
            params = req.params or {}
        elif req.strategy and req.strategy in strategies.BUILTIN:
            spec = strategies.BUILTIN[req.strategy]
            strat_cls = spec["cls"]
            params = {**spec["params"], **(req.params or {})}
        else:
            raise HTTPException(400, "strategy 또는 custom_code 가 필요합니다.")

        result = run_backtest(
            df, strat_cls, params,
            cash=req.cash, commission=req.commission,
        )
        result["name"] = data.resolve_name(req.code)
        return result
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"백테스트 실패: {e}")


# ---- 프론트엔드 정적 서빙 ----
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))
