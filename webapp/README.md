# My Backtest Tool (MVP)

트레이딩뷰 스타일의 개인용 백테스트 웹툴. 종목 검색 → 캔들차트 → 전략 실행 →
성과지표 + 차트에 진입/청산 마커 표시.

## 구성
```
webapp/
├─ backend/
│  ├─ main.py          FastAPI 앱 (API + 프론트 서빙)
│  ├─ data.py          종목검색 + OHLCV (FinanceDataReader + SQLite 캐시)
│  ├─ strategies.py    내장 전략 레지스트리 + 커스텀 코드 로더
│  └─ engine.py        backtesting.py 래퍼 (지표 + 마커 + 자본곡선)
├─ frontend/
│  └─ index.html       TradingView Lightweight Charts UI (단일 파일)
└─ requirements.txt
```

## 실행
```bash
pip install -r webapp/requirements.txt
cd webapp/backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# 브라우저에서 http://localhost:8000
# 외부 공유: ngrok http 8000
```

## API
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/search?q=삼성` | 종목 검색 (이름/코드) |
| GET | `/api/ohlcv?code=005930&start=2020-01-01` | 캔들 데이터 |
| GET | `/api/strategies` | 내장 전략 목록 + 기본 파라미터 |
| POST | `/api/backtest` | 전략 실행 → `{metrics, markers, equity}` |

## 전략 추가하기
`backend/strategies.py` 에서 `backtesting.py` 의 `Strategy` 를 상속하고 `BUILTIN` 에 등록.
기존 `tradingview/*.pine` 전략(Sperandeo 1-2-3, 2B 등)을 이 형태로 이식하면 드롭다운에 자동 노출.

UI 의 "직접 입력" 탭에서 파이썬 Strategy 코드를 바로 붙여 실행할 수도 있음.
> ⚠️ 커스텀 코드는 `exec` 로 실행됨 — 신뢰할 수 있는 로컬 환경에서만 공개(ngrok)하세요.

## 데이터
- 기본: 요청 시 FinanceDataReader 로 조회 후 `backend/cache.db`(SQLite)에 캐시.
- 대량 사전적재: `scripts/fetch_kospi_ohlcv_fdr.py` 로 받은 CSV 를 캐시에 넣어 오프라인 사용 가능.

## 로드맵
- [ ] Sperandeo 1-2-3 / 2B 전략 파이썬 이식
- [ ] 지표 오버레이(이평선 등) 차트 표시
- [ ] 파라미터 최적화(그리드 서치)
- [ ] 자주 쓰는 Pine 함수 부분집합 파서 (Pine → 파이썬)
