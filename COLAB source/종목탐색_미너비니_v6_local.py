#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
주도종목 필터링 및 평가 (미너비니 트렌드 템플릿) — 로컬 실행용

원본 Colab 노트북(v6)을 로컬에서 실행되도록 변환한 스크립트.

[변경 사항]
- 등락률(change_ratio)을 close 와 동일한 FinanceDataReader 시계열에서 직접 계산
  (기존: fdr.StockListing 스냅샷의 'ChagesRatio' → close 와 기준일 불일치 문제 수정)
- Colab 전용 코드(google.colab drive/userdata, !pip)를 로컬용으로 대체/주석 처리

[사전 준비]
- 같은 폴더에 Quant.py, config.yaml 이 있어야 합니다.
- 패키지 설치:
    pip install finance-datareader pykrx slack_sdk pyyaml requests pandas matplotlib seaborn openpyxl beautifulsoup4 lxml
- (선택) 슬랙 알림: 환경변수 SLACK_TOKEN 설정

[실행] python 종목탐색_미너비니_v6_local.py
  → 네트워크/API 호출이 많으므로 시간이 걸립니다. 필요 섹션만 남기고 다른 섹션은 주석 처리해 사용하세요.
"""

# IPython display() 대체 (로컬에서도 동작)
try:
    from IPython.display import display
except Exception:
    def display(*args, **kwargs):
        for a in args:
            print(a)


# ====================================================================
# # 주도종목 필터링 및 평가
#  - 리포트서머리 포함 버전
# ====================================================================

# ----- [cell 1] ----------------------------------------
# !pip install pykrx

# ----- [cell 2] ----------------------------------------
# !pip install -U finance-datareader

# ----- [cell 3] ----------------------------------------
# !pip install slack_sdk

# ----- [cell 4] ----------------------------------------
# [로컬 변환] Colab 드라이브 마운트/복사 대신, 현재 폴더의 config.yaml / Quant.py 사용
import os
for file_name in ['config.yaml', 'Quant.py']:
    if os.path.exists(file_name):
        print(f'✅ {file_name} 확인')
    else:
        print(f'❌ {file_name} 이(가) 현재 폴더에 없습니다. 같은 폴더에 두세요.')

# ----- [cell 5] ----------------------------------------
# 패키지 개발에 필요한 외부 패키지 import

import pandas as pd
import time
import requests
import json
from datetime import datetime
import yaml
import FinanceDataReader as fdr
from pykrx import stock as pystock

from dateutil.relativedelta import relativedelta
import os

# 파일이 복사된 후 다시 import 시도
from Quant import Quant

with open('config.yaml', 'r') as f:
    config = yaml.load(f, Loader=yaml.FullLoader)

api_key = config['hantu']['api_key']
s_key = config['hantu']['secret_key']
account_id = config['hantu']['account_id']

qt = Quant(api_key=api_key, secret_key=s_key, account_id=account_id)

# ----- [cell 6] ----------------------------------------
from slack_sdk import WebClient
import os

# [로컬 변환] Colab Secrets(userdata) 대신 환경변수 SLACK_TOKEN 사용
slack_token = os.environ.get('SLACK_TOKEN')
channel_id = 'C0A98H6E96H'
if slack_token:
    client = WebClient(token=slack_token)
    qt.client = client
    if not hasattr(qt, '_original_post_message'):
        qt._original_post_message = qt.post_message
    def patched_post_message(message, channel_id=None):
        target_channel = channel_id if channel_id else 'C0A98H6E96H'
        try:
            qt.client.chat_postMessage(channel=target_channel, text=message)
        except Exception as e:
            print(f"메시지 전송 실패: {e}")
    qt.post_message = patched_post_message
    print("✅ Slack Client 연결 완료 (환경변수 SLACK_TOKEN)")
else:
    print("ℹ SLACK_TOKEN 환경변수가 없어 슬랙 알림은 비활성화됩니다.")

# ----- [cell 7] ----------------------------------------
def check_minervini(ticker, rs_score=None):
    try:
        df = qt.get_past_data(ticker, n=300)

        if len(df) < 200:
            return False, None, None, None

        ma_50  = df['close'].rolling(window=50).mean().iloc[-1]
        ma_150 = df['close'].rolling(window=150).mean().iloc[-1]
        ma_200 = df['close'].rolling(window=200).mean().iloc[-1]

        if len(df) < 200 + 22: # 1개월(22거래일) 여유 확보
            return False, None, None, None

        ma_200_series = df['close'].rolling(window=200).mean()
        ma_200_now    = ma_200_series.iloc[-1]
        ma_200_1m_ago = ma_200_series.iloc[-22]

        ma_200_last_22 = ma_200_series.iloc[-22:]
        up_ratio = (ma_200_last_22.diff().dropna() > 0).mean()

        condition_ma200_trend = (
            ma_200_now > ma_200_1m_ago and
            up_ratio >= 0.7 #기간내 70%이상 상승일
        )

        vol_ma_5 = df['volume'].rolling(window=5).mean().iloc[-1]

        if len(df) < 260:
            return False, None, None, None
        recent_260 = df.tail(260)

        high_52w = recent_260['high'].max()
        low_52w  = recent_260['low'].min()

        current_price = df['close'].iloc[-1]
        volume        = df['volume'].iloc[-1]

        # [수정: 방법 A] 등락률(전일대비)을 종가와 '동일한' FDR 시계열에서 직접 계산
        prev_close   = df['close'].iloc[-2]
        change_ratio = (current_price / prev_close - 1) * 100 if prev_close else 0.0

        condition = (
            current_price > ma_150 and
            current_price > ma_200 and
            ma_150 > ma_200 and
            condition_ma200_trend and
            ma_50 > ma_150 and
            ma_50 > ma_200 and
            current_price > ma_50 and
            current_price >= (low_52w * 1.3) and
            current_price >= (high_52w * 0.75) and
            volume > vol_ma_5 and
            (rs_score is not None and rs_score >= 50)
        )

        return condition, volume, current_price, change_ratio

    except Exception:
        return False, 0, 0, 0


# --- 252일 수익률 수집 ---
def get_252d_return(ticker):
    try:
        df = qt.get_past_data(ticker, n=260)
        if len(df) < 252:
            return None
        return df['close'].iloc[-1] / df['close'].iloc[-252] - 1
    except Exception:
        return None


# --- 종목 발굴 메인 로직 ---
def search_minervini_stocks():
    print("조건 종목 탐색 시작...")

    try:
        df_cap = fdr.StockListing('KRX')
    except Exception as e:
        print(f"종목 리스트 조회 실패: {e}")
        return [], pd.DataFrame()

    if df_cap.empty:
        print("가져온 데이터가 없습니다.")
        return [], pd.DataFrame()

    df_cap = df_cap[df_cap['Amount'] > 0]

    target_df = df_cap[
        (df_cap['Marcap'] >= 300_000_000_000) &
        (df_cap['Amount'] >= 30_000_000_000)
    ]

    print(f"1차 필터링 (시총 3000억↑, 거래대금 300억↑): {len(target_df)}개 종목 발견")

    print("RS 점수 계산을 위한 252일 수익률 수집 중...")
    returns_map = {}
    total_count = len(target_df)
    for i, row in enumerate(target_df.itertuples()):
        if i % 50 == 0:
            print(f"... 수익률 수집 {i}/{total_count}")
        r = get_252d_return(row.Code)
        if r is not None:
            returns_map[row.Code] = r

    if not returns_map:
        print("252일 수익률 데이터를 충분히 수집하지 못했습니다.")
        return [], pd.DataFrame()

    rs_df = pd.DataFrame({
        'Ticker': list(returns_map.keys()),
        'Return_252D': list(returns_map.values()),
    })
    rs_df['RS_Score'] = rs_df['Return_252D'].rank(pct=True) * 100
    rs_map = dict(zip(rs_df['Ticker'], rs_df['RS_Score']))

    print(f"RS 점수 계산 완료: {len(rs_map)}개 / RS≥50 종목 수: {(rs_df['RS_Score']>=50).sum()}")

    # RS 50 이상만 통과 가능
    final_list = []
    for i, row in enumerate(target_df.itertuples()):
        ticker = row.Code
        name   = row.Name

        if i % 50 == 0:
            print(f"... RS 체크 {i}/{total_count}")

        rs_score = rs_map.get(ticker)
        if rs_score is None or rs_score < 50:   #70 -> 50으로 완화
            continue

        is_match, volume, close_price, change_ratio = check_minervini(ticker, rs_score=rs_score)

        if is_match:
            if volume is None or close_price is None:
                continue

            amount_raw     = volume * close_price
            amount_million = amount_raw / 1_000_000
            print(f"조건 만족! : {name} ({ticker}) / RS:{rs_score:.1f} / 거래대금:{amount_million:,.0f}백만")

            final_list.append({
                'ticker'      : ticker,
                'name'        : name,
                'volume'      : volume,
                'close'       : close_price,
                'amount'      : amount_million,
                'rs_score'    : round(rs_score, 1),
                'return_252d' : round(returns_map[ticker] * 100, 2),
                'change_ratio': round(change_ratio, 2) if change_ratio is not None else 0,
            })

    if not final_list:
        print("조건을 만족하는 종목이 없습니다.")
        return [], pd.DataFrame()

    df_result = pd.DataFrame(final_list)
    df_result = df_result.sort_values(by='amount', ascending=False)

    print("\n=== 선정 최종 리스트 (거래대금 순 Top 50) ==")
    pd.options.display.float_format = '{:,.0f}'.format
    print(df_result[['ticker', 'name', 'amount', 'rs_score', 'return_252d',
                     'change_ratio', 'close', 'volume']].head(50))

    top20_tickers = df_result['ticker'].head(50).tolist()
    return top20_tickers, df_result.head(50)

# ====================================================================
# # 종목 탐색
# ====================================================================

# ----- [cell 9] ----------------------------------------
print("종목 탐색을 수행합니다...")
found_stocks_tickers, df_found_stocks = search_minervini_stocks()

if found_stocks_tickers:
    stock_list_str = ", ".join(found_stocks_tickers)
    qt.post_message(f"전체 {len(found_stocks_tickers)}개 종목 선정: {stock_list_str}")
    print(f"탐색 결과: {found_stocks_tickers}")

    # 상위 50개 종목 상세 정보를 Slack으로 전송
    if not df_found_stocks.empty:
        slack_message_parts = ["*종목 탐색 최종 리스트 (거래대금 순 Top 20)*"]
        # DataFrame 인덱스와 관계없이 순차적 인덱스(i)를 얻기 위해 enumerate 사용
        for i, (idx, row) in enumerate(df_found_stocks.head(50).iterrows()):
            change_ratio = row.get('change_ratio', 0)
            slack_message_parts.append(
                f"▫ {row['name']} ({row['ticker']}) - 거래대금: {row['amount']:,.0f}백만 (등락률: {change_ratio:+.2f}%)"
            )
            if (i + 1) % 10 == 0 and (i + 1) < 50:
                slack_message_parts.append("===========================================================")
        slack_message = "\n".join(slack_message_parts)
        qt.post_message(slack_message)
else:
    print("탐색 결과: 조건에 맞는 종목이 없습니다.")
    qt.post_message("[테스트 모드] 조건에 맞는 종목이 없습니다.")

target_stocks = found_stocks_tickers

# ----- [cell 10] ----------------------------------------
print("\n=== 10일 이동평균선 상향 돌파 종목 검색 시작 ===")

crossover_stocks = []

# df_found_stocks가 있다면 이름 매핑을 미리 생성 (효율성 및 정합성 확보)
name_map = {}
if 'df_found_stocks' in globals() and not df_found_stocks.empty:
    # ticker를 문자열로 확실하게 변환하여 매핑
    name_map = df_found_stocks.set_index('ticker')['name'].to_dict()

if 'target_stocks' not in globals() or not target_stocks:
    print("⚠️ 'target_stocks' 리스트가 비어있거나 정의되지 않았습니다. 이전 단계에서 종목 선별이 필요합니다.")
else:
    for ticker in target_stocks:
        try:
            # qt 객체 사용으로 수정
            df = qt.get_past_data(ticker, n=30) # 최근 30일 데이터만 가져와도 10일선 계산에 충분

            if len(df) < 10: # 10일 이동평균선을 계산할 수 없는 경우 제외
                continue

            # 10일 이동평균선 계산
            df['MA_10'] = df['close'].rolling(window=10).mean()

            # 오늘의 종가와 어제의 종가, 그리고 10일선 값 가져오기
            today_close = df['close'].iloc[-1]
            yesterday_close = df['close'].iloc[-2]
            today_ma10 = df['MA_10'].iloc[-1]
            yesterday_ma10 = df['MA_10'].iloc[-2]

            # 조건 확인: 어제 종가가 10일선 아래, 오늘 종가가 10일선 위
            if yesterday_close < yesterday_ma10 and today_close > today_ma10:
                # 이름 가져오기: 매핑에 있으면 사용, 없으면 StockListing 조회 (비상용)
                stock_name = name_map.get(str(ticker))
                if not stock_name:
                     stock_name = fdr.StockListing('KRX').loc[fdr.StockListing('KRX')['Code'] == ticker, 'Name'].iloc[0]

                crossover_stocks.append({
                    'ticker': ticker,
                    'name': stock_name,
                    'today_close': today_close,
                    'today_ma10': today_ma10
                })

        except Exception as e:
            # print(f"Error processing {ticker}: {e}") # 디버깅 시 주석 해제
            continue

    if crossover_stocks:
        df_crossover = pd.DataFrame(crossover_stocks)
        print("\n=== 10일 이동평균선 상향 돌파 종목 ===")
        display(df_crossover)
        # Slack 메시지 전송
        slack_message_parts = ["*📈 10일 이동평균선 상향 돌파 종목 발견!*\n"]
        for index, row in df_crossover.iterrows():
            slack_message_parts.append(f"▫ {row['name']} ({row['ticker']}) - 오늘 종가: {row['today_close']:,.0f}, 10일선: {row['today_ma10']:,.0f}")
        qt.post_message("\n".join(slack_message_parts))

    else:
        print("조건을 만족하는 10일 이동평균선 상향 돌파 종목이 없습니다.")
        qt.post_message("조건을 만족하는 10일 이동평균선 상향 돌파 종목이 없습니다.")

# ----- [cell 11] ----------------------------------------
# 코스피/코스닥 전체 종목 중 거래대금 1조원 이상 종목 필터링
try:
    import FinanceDataReader as fdr
    import pandas as pd

    # KRX 전체 종목 리스트 가져오기
    df_all_krx = fdr.StockListing('KRX')

    # 거래대금(Amount)이 1조 원 이상인 종목 필터링
    trillion_club = df_all_krx[df_all_krx['Amount'] >= 1_000_000_000_000].copy()

    # 실제 존재하는 등락률 컬럼명 찾기 (ChangesRatio, ChgRate, ChangeRatio 등 대응)
    possible_cols = ['ChangesRatio', 'ChgRate', 'ChangeRatio', '등락률']
    actual_chg_col = next((col for col in possible_cols if col in trillion_club.columns), None)

    print(f"\n=== [전체 시장 거래대금 1조 원 이상 종목] 총 {len(trillion_club)}개 발견 ===")

    if not trillion_club.empty:
        trillion_club = trillion_club.sort_values(by='Amount', ascending=False)

        # 출력할 컬럼 리스트 구성
        display_cols = ['Code', 'Name', 'Amount', 'Close']
        if actual_chg_col:
            display_cols.append(actual_chg_col)

        # 백만 단위 거래대금 추가
        trillion_club['Amount_Million'] = trillion_club['Amount'] / 1_000_000
        final_display_cols = ['Code', 'Name', 'Amount_Million'] + ([actual_chg_col] if actual_chg_col else []) + ['Close']

        display(trillion_club[final_display_cols])

        # 슬랙 메시지 전송
        names = ", ".join(trillion_club['Name'].tolist())
        if 'qt' in globals():
            qt.post_message(f"🔥 [전체 시장 1조 클럽 포착] {len(trillion_club)}종목: {names}")
    else:
        print("오늘 전 종목 중 거래대금이 1조 원을 넘는 종목이 없습니다.")
except Exception as e:
    import traceback
    print(f"데이터 조회 중 오류 발생: {e}")
    traceback.print_exc()

# ====================================================================
# # 테마의 지속성 스코어링
# ====================================================================

# ----- [cell 13] ----------------------------------------
import requests
import pandas as pd
import io
import re

def get_quant_factors(stock_code):
    """
    네이버 금융에서 EPS 성장률, 연속 순매수, 거래대금 급증율을 추출
    """
    headers = {'User-Agent': 'Mozilla/5.0'}

    is_turnaround = False
    eps_upgrade_rate = 0.0
    credit_ratio = 0.0
    foreign_streak, inst_streak, surge_rate = 0, 0, 0.0

    # ---------------------------------------------------------
    # 1. 신용잔고율
    # ---------------------------------------------------------
    main_url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    main_response = requests.get(main_url, headers=headers)

    try:
        crd_block_match = re.search(r'신용비율.*?<td[^>]*>(.*?)</td>', main_response.text, re.DOTALL)
        if crd_block_match:
            td_content = crd_block_match.group(1)
            num_match = re.search(r'([\d\.]+)', td_content)
            if num_match:
                credit_ratio = float(num_match.group(1))
    except Exception as e:
        pass # 에러 발생 시 기본값 0.0 유지

    # ---------------------------------------------------------
    # 2. EPS 성장률 (종목분석 페이지 - 주요재무정보 표 활용)
    # ---------------------------------------------------------
    main_url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    main_response = requests.get(main_url, headers=headers)

    try:
        # 네이버 금융 메인 페이지 하단의 재무제표 탭이 가장 빠르고 안정적임
        finance_tables = pd.read_html(io.StringIO(main_response.text), match='주요재무정보', encoding='euc-kr')

        if finance_tables:
            fin_df = finance_tables[0]

            # 멀티인덱스 컬럼 평탄화 (불필요한 최상단 레벨 제거)
            fin_df.columns = fin_df.columns.droplevel([0, 2])
            fin_df.set_index('주요재무정보', inplace=True)

            # 'EPS(원)' 행 찾기
            eps_row = fin_df.loc[fin_df.index.str.contains('EPS', na=False)]

            if not eps_row.empty:
                # 최근 4개 분기 또는 연간 데이터 중 유효한 숫자만 추출
                eps_values = pd.to_numeric(eps_row.iloc[0].astype(str).str.replace(',', ''), errors='coerce').dropna().values

                # 최소 2개의 데이터(최근 수치와 직전 수치)가 있어야 비교 가능
                if len(eps_values) >= 2:
                    current_eps = eps_values[-1] # 가장 최근 또는 예상 EPS
                    prev_eps = eps_values[-2]    # 직전 EPS

                    # 턴어라운드 (적자 -> 흑자 전환) 조건
                    if prev_eps < 0 and current_eps > 0:
                        is_turnaround = True

                    if prev_eps > 0: # 분모가 0보다 클 때만 상승률 계산 (흑자 전환 등은 별도 로직 필요)
                        eps_upgrade_rate = round(((current_eps - prev_eps) / prev_eps) * 100, 2)
    except Exception as e:
        print(f"[{stock_code}] EPS 데이터 처리 중 오류: {e}")

    # ---------------------------------------------------------
    # 3. 연속 순매수 & 거래대금 급증율 (투자자별 매매동향 페이지)
    # ---------------------------------------------------------
    frgn_url = f"https://finance.naver.com/item/frgn.naver?code={stock_code}"
    frgn_response = requests.get(frgn_url, headers=headers)

    try:
        tables = pd.read_html(io.StringIO(frgn_response.text), match='순매매량', encoding='euc-kr')

        if not tables:
            return eps_upgrade_rate, foreign_streak, inst_streak, surge_rate, credit_ratio

        investor_df = tables[0]

        if len(investor_df.columns) == 9:
            investor_df.columns = ['날짜', '종가', '전일비', '등락률', '거래량', '기관', '외국인', '보유주수', '보유율']
        else:
            return eps_upgrade_rate, foreign_streak, inst_streak, surge_rate, credit_ratio

        investor_df = investor_df[investor_df['날짜'].astype(str).str.match(r'\d{4}\.\d{2}\.\d{2}')].copy()

        def count_consecutive_buy(series):
            count = 0
            for val in series:
                if pd.isna(val) or str(val).strip().lower() == 'nan':
                    continue
                clean_val = str(val).replace(',', '').replace('+', '').strip()
                try:
                    if float(clean_val) > 0:
                        count += 1
                    else:
                        break
                except ValueError:
                    break
            return count

        foreign_streak = count_consecutive_buy(investor_df['외국인'])
        inst_streak = count_consecutive_buy(investor_df['기관'])

        investor_df['종가_num'] = pd.to_numeric(investor_df['종가'].astype(str).str.replace(',', ''), errors='coerce')
        investor_df['거래량_num'] = pd.to_numeric(investor_df['거래량'].astype(str).str.replace(',', ''), errors='coerce')
        investor_df['거래대금'] = investor_df['종가_num'] * investor_df['거래량_num']

        valid_trading_val = investor_df['거래대금'].dropna().values

        if len(valid_trading_val) >= 6:
            current_val = valid_trading_val[0]
            past_5d_avg = valid_trading_val[1:6].mean()
            if past_5d_avg > 0:
                surge_rate = round((current_val / past_5d_avg) * 100, 2)

    except Exception as e:
        print(f"[{stock_code}] 매매동향 처리 중 오류: {e}")

    return eps_upgrade_rate, is_turnaround, foreign_streak, inst_streak, surge_rate, credit_ratio

# ----- [cell 14] ----------------------------------------
import pandas as pd
import time  # 서버 차단 방지를 위한 딜레이 모듈 추가

print("=== 거래대금 상위 20개 종목 퀀트 팩터(수급 및 과열) 현황 분석 ===")

# df_found_stocks 변수가 존재하는지 확인
if 'df_found_stocks' in globals() and not df_found_stocks.empty:
    # 이미 amount(거래대금) 순으로 정렬되어 있으므로 head(20)으로 상위 20개 추출
    top20_df = df_found_stocks.head(20).copy()

    trend_results = []
    print(f"총 {len(top20_df)}개 종목에 대한 크롤링을 시작합니다.")
    print("네이버 금융 서버 부하 방지를 위해 약 10~15초 정도 소요됩니다...")

    for index, row in top20_df.iterrows():
        ticker = str(row['ticker']).zfill(6)
        name = row['name']
        amount = row['amount']

        # 1. 6가지 핵심 퀀트 팩터 동시 크롤링 (반환값 개수 예외 처리)
        factors = get_quant_factors(ticker)
        if len(factors) == 5:
            eps_rt, f_streak, i_streak, surge_rt, credit_rt = factors
            is_turnaround = False
        else:
            eps_rt, is_turnaround, f_streak, i_streak, surge_rt, credit_rt = factors

        # 2. 턴어라운드 여부에 따른 EPS 표기 로직 적용 (문자열 또는 숫자)
        eps_display = "Turnaround" if is_turnaround else eps_rt

        trend_results.append({
            '종목명': name,
            '종목코드': ticker,
            '거래대금(백만)': amount,
            'EPS 성장률(%)': eps_display,  # 3. 조건부 표기 변수 적용
            '외국인 연속 매수(일)': f_streak,
            '기관 연속 매수(일)': i_streak,
            '거래대금 급증율(%)': surge_rt,
            '신용잔고율(%)': credit_rt
        })

        # 봇(Bot)으로 인식되어 IP가 차단되는 것을 막기 위한 0.5초 딜레이
        time.sleep(0.5)

    # 결과를 DataFrame으로 변환 및 출력
    trend_df = pd.DataFrame(trend_results)

    # 보기 좋게 포맷팅 (비율 데이터가 있으므로 소수점 둘째 자리까지 표시하도록 조정)
    # 문자열("Turnaround")이 들어간 열은 이 포맷팅의 영향을 받지 않고 그대로 안전하게 출력됩니다.
    pd.options.display.float_format = '{:,.2f}'.format
    display(trend_df)

    # Slack 메시지 구성 및 전송
    # slack_msg = ["*거래대금 TOP 20 퀀트 팩터 분석 리포트*"]
    # for idx, r in trend_df.iterrows():
    #     eps_str = f"{r['EPS 성장률(%)']}" if isinstance(r['EPS 성장률(%)'], str) else f"{r['EPS 성장률(%)']:,.1f}%"
    #     msg = (f"▫ *{r['종목명']}* ({r['종목코드']})\n"
    #            f"  - EPS성장: {eps_str} / 수급: 외인({r['외국인 연속 매수(일)']}), 기관({r['기관 연속 매수(일)']})\n"
    #            f"  - 거래급증: {r['거래대금 급증율(%)']:,.1f}% / 신용: {r['신용잔고율(%)']}% / 대금: {r['거래대금(백만)']:,.0f}백만")
    #     slack_msg.append(msg)

    # qt.post_message("\n".join(slack_msg))
    # print("Slack 메시지 전송 완료")

else:
    print("데이터가 없습니다. 먼저 종목 탐색(search_minervini_stocks) 셀을 실행하여 df_found_stocks를 생성해주세요.")

# ----- [cell 15] ----------------------------------------
import pandas as pd
import os
from datetime import datetime, timedelta

# 1. 오늘 포착된 종목 데이터 확인
if 'df_found_stocks' in globals() and not df_found_stocks.empty:
    today_top20 = df_found_stocks.head(20).copy()

    # 2. 리포트 데이터 로드 (모든 시트 통합)
    file_path = '/content/drive/MyDrive/Colab Notebooks/리포트서머리.xlsx'
    if os.path.exists(file_path):
        # sheet_name=None 설정을 통해 모든 시트를 딕셔너리 형태로 로드
        df_dict = pd.read_excel(file_path, sheet_name=None)

        # 모든 시트의 데이터를 하나로 합침
        df_report = pd.concat(df_dict.values(), ignore_index=True)

        # 컬럼 매핑 (통합된 데이터프레임에서 수행)
        date_col = next((c for c in df_report.columns if any(k in str(c) for k in ['날짜', 'Date', '수집'])), None)
        stock_col = next((c for c in df_report.columns if any(k in str(c) for k in ['종목', 'Stock', '기업'])), None)
        target_col = next((c for c in df_report.columns if '목표' in str(c) or 'Target' in str(c)), None)

        if date_col and stock_col and target_col:
            # 데이터 전처리: 날짜 변환 및 목표가 수치화
            df_report[date_col] = pd.to_datetime(df_report[date_col], errors='coerce')
            df_report[target_col] = pd.to_numeric(df_report[target_col], errors='coerce')

            # 유효하지 않은 행(날짜나 목표가 없는 경우) 제거
            df_report = df_report.dropna(subset=[date_col, target_col])

            one_month_ago = datetime.now() - timedelta(days=30)

            # 최근 한 달 리포트 필터링
            df_recent_reports = df_report[df_report[date_col] >= one_month_ago].copy()

            # 3. 매칭 및 평균가 계산 로직
            matched_results = []

            for _, today_row in today_top20.iterrows():
                t_name = today_row['name']
                t_close = today_row['close']

                # 종목명 포함 여부 확인
                mask = df_recent_reports[stock_col].str.replace(" ", "").str.contains(t_name.replace(" ", ""), na=False)
                target_reports = df_recent_reports[mask]

                if not target_reports.empty:
                    # 유효한 목표가 기반 평균 계산
                    report_count = len(target_reports)
                    avg_target_price = target_reports[target_col].mean()
                    gap_ratio = ((avg_target_price - t_close) / t_close) * 100

                    matched_results.append({
                        '종목명': t_name,
                        '현재가': t_close,
                        '건수': report_count,
                        '평균목표가': avg_target_price,
                        '괴리율': gap_ratio
                    })

            # 4. 결과 출력 및 Slack 전송 (표 형태)
            if matched_results:
                df_matched = pd.DataFrame(matched_results)
                df_matched = df_matched.sort_values(by='건수', ascending=False)

                print(f"\n=== 모든 시트 통합 분석 결과 ({len(df_matched)}종목 매칭) ===")
                display(df_matched)

                # 슬랙용 텍스트 표 생성 (Fixed-width)
                header = f"{'종목명':<10} | {'건수':<3} | {'평균목표가':>10} | {'괴리율':>7}"
                separator = "-" * len(header)
                rows = []
                for _, r in df_matched.iterrows():
                    rows.append(f"{r['종목명']:<10} | {int(r['건수']):<3} | {r['평균목표가']:>10,.0f} | {r['괴리율']:>6.1f}%")

                table_str = "\n".join([header, separator] + rows)
                slack_message = f"*🔍 전 시트 통합 리포트 분석 (최근 1개월)*\n```\n{table_str}\n```"

                qt.post_message(slack_message)
            else:
                print("최근 한 달 리포트 중 오늘 포착된 종목과 일치하는 데이터가 없습니다.")
        else:
            print("리포트 엑셀의 필수 컬럼을 찾을 수 없습니다.")
    else:
        print("리포트 파일을 찾을 수 없습니다.")
else:
    print("오늘 포착된 종목 데이터(df_found_stocks)가 없습니다.")

# ----- [cell 16] ----------------------------------------
import pandas as pd
import time

def calculate_theme_longevity_live(df):
    """
    top20_df의 실제 종목들을 순회하며 실시간 퀀트 팩터를 수집하고 계단식 테마 지속성 점수 및 턴어라운드 가점을 계산하는 함수
    (캔들스틱 패턴 및 상승률 가점 로직 추가)
    """
    results = []

    # 리포트 괴리율 데이터 사전 준비 (df_matched가 존재할 경우)
    gap_bonus_map = {}
    if 'df_matched' in globals() and not df_matched.empty:
        # 괴리율이 20% 이상인 종목들만 추출하여 맵 생성
        gap_bonus_map = df_matched[df_matched['괴리율'] >= 20].set_index('종목명')['괴리율'].to_dict()

    print(f"총 {len(df)}개 종목에 대한 실시간 스코어링을 시작합니다.")
    print("네이버 금융 서버 부하 방지를 위해 약 10~15초 정도 소요됩니다...")

    for index, row in df.iterrows():
        score = 0
        reasons = []

        # 1. 데이터 매핑
        stock_code = str(row['ticker']).zfill(6)
        stock_name = row['name']
        change_ratio = row.get('change_ratio', 0)

        # --- [추가] 캔들스틱 및 주가 모멘텀 기반 스코어링 ---
        try:
            # 당일 OHLC 데이터 조회
            past_data = qt.get_past_data(stock_code, n=2)
            if not past_data.empty:
                today_open = past_data['open'].iloc[-1]
                today_high = past_data['high'].iloc[-1]
                today_low = past_data['low'].iloc[-1]
                today_close = past_data['close'].iloc[-1]

                if today_open > 0:
                    body_pct = ((today_close - today_open) / today_open) * 100

                    upper_shadow_val = today_high - max(today_close, today_open)
                    upper_shadow_pct = (upper_shadow_val / today_open) * 100
                    body_val = abs(today_close - today_open)

                    # 장대양봉본체(시가→종가) ≥ +10% (+2), 중간 양봉본체 +5 ~ +10% (+1), 큰 음봉본체 ≤ -3% (-1)
                    if body_pct >= 10:
                        score += 2
                        reasons.append(f"장대양봉 ({body_pct:.1f}%)")
                    elif body_pct >= 5:
                        score += 1
                        reasons.append(f"중간양봉 ({body_pct:.1f}%)")
                    elif body_pct <= -3:
                        score -= 1
                        reasons.append(f"큰 음봉 ({body_pct:.1f}%)")

                    # 긴 윗꼬리: upper_wick > body×1.5 & ≥ 1.5% (-1)
                    if upper_shadow_val > (body_val * 1.5) and upper_shadow_pct >= 1.5:
                        score -= 1
                        reasons.append(f"긴 윗꼬리 경계 ({upper_shadow_pct:.1f}%)")
        except Exception as e:
            pass

        # 절대 상승률 가점
        if change_ratio >= 15:
            score += 2
            reasons.append(f"폭등(대장주) ({change_ratio:+.1f}%)")
        elif change_ratio >= 7:
            score += 1
            reasons.append(f"강한 상승 ({change_ratio:+.1f}%)")

        # 2. 실시간 통합 퀀트 팩터 크롤링 (5개 반환 vs 6개 반환 예외 처리)
        factors = get_quant_factors(stock_code)
        if len(factors) == 5:
            eps_rt, f_streak, i_streak, surge_rt, credit_rt = factors
            is_turnaround = False
        else:
            eps_rt, is_turnaround, f_streak, i_streak, surge_rt, credit_rt = factors

        # 3. Long-term 가점 요소 (펀더멘털)
        if is_turnaround:
            score += 3
            reasons.append("EPS 흑자 전환 (턴어라운드)")
        else:
            if eps_rt >= 100.0:
                score += 5
                reasons.append(f"EPS 트리플 자릿수 폭발(+{eps_rt}%)")
            elif eps_rt >= 50.0:
                score += 4
                reasons.append(f"EPS 초고도 성장(+{eps_rt}%)")
            elif eps_rt >= 20.0:
                score += 3
                reasons.append(f"EPS 고성장(+{eps_rt}%)")
            elif eps_rt >= 5.0:
                score += 2
                reasons.append(f"EPS 안정적 상향(+{eps_rt}%)")

        # 리포트 목표가 괴리율 가점
        if stock_name in gap_bonus_map:
            score += 2
            reasons.append(f"리포트 목표가 괴리율 우수({gap_bonus_map[stock_name]:.1f}%)")

        # 수급 로직 유지
        if f_streak >= 3:
            score += 2
            reasons.append(f"외인 {f_streak}일 연속 매수")

        if i_streak >= 3:
            score += 2
            reasons.append(f"기관 {i_streak}일 연속 매수")

        # 4. Short-term 감점 요소 (단기 투기성 및 과열 지표)
        if surge_rt >= 300.0:
            score -= 2
            reasons.append(f"거래대금 과열({surge_rt}%)")

        if credit_rt >= 5.0:
            score -= 2
            reasons.append(f"신용잔고 과다({credit_rt}%)")

        # 5. 최종 스코어링 기반 등급 분류
        if score >= 6:
            classification = "🟢 [Long-term] 슈퍼 스톡 후보 (강력 홀딩)"
        elif score >= 3:
            classification = "🟡 [Mid-term] 구조적 모멘텀 유지 (트레일링 스탑 요망)"
        elif score >= 0:
            classification = "🟠 [Short-term] 수급 주도 테마 (단기 대응)"
        else:
            classification = "🔴 [Warning] 투기성 과열 경계 (비중 축소)"

        results.append({
            '종목명': stock_name,
            '종목코드': stock_code,
            '분류': classification,
            'Total Score': score,
            'EPS 상향률(%)': "턴어라운드" if is_turnaround else eps_rt,
            '외인 연속매수': f_streak,
            '기관 연속매수': i_streak,
            '거래대금 급증율(%)': surge_rt,
            '신용잔고율(%)': credit_rt,
            '핵심 근거': " / ".join(reasons) if reasons else "특이사항 없음 (중립)"
        })

        # 봇(Bot) 차단 방지용 딜레이
        time.sleep(0.5)

    return pd.DataFrame(results)

# --- 실행 부분 ---
if 'top20_df' in globals() and not top20_df.empty:
    report_df = calculate_theme_longevity_live(top20_df)

    # 점수(Total Score)가 높은 순으로 데이터프레임 정렬
    report_df = report_df.sort_values(by='Total Score', ascending=False).reset_index(drop=True)

    print("\n" + "="*90)
    print("[라파엔 퀀트 시그널] 슈퍼 스톡 필터링 실시간 스코어링 리포트")
    print("="*90)

    for index, row in report_df.iterrows():
        print(f"[{row['종목명']}] {row['분류']} (Score: {row['Total Score']})")
        print(f"  👉 근거: {row['핵심 근거']}\n")

    # Slack 메시지 전송
    slack_parts = ["*[라파엔 퀀트 시그널] 실시간 스코어링 리포트*"]
    for index, row in report_df.iterrows():
        slack_parts.append(f"▫ *{row['종목명']}* ({row['Total Score']}점): {row['분류']}\n  - {row['핵심 근거']}")

    if 'qt' in globals() and hasattr(qt, 'post_message'):
        qt.post_message("\n".join(slack_parts))

else:
    print("분석할 top20_df 데이터가 없습니다. 종목 탐색 셀을 먼저 실행해 주세요.")

# ====================================================================
# #엑셀 저장
# ====================================================================

# ----- [cell 18] ----------------------------------------
import os
import pandas as pd
from datetime import datetime
import FinanceDataReader as fdr

def save_to_excel():
    print("엑셀 저장 작업을 시작합니다...")

    drive_path = '/content/drive/MyDrive/Colab Notebooks/종목탐색_TOP30.xlsx'

    if 'df_found_stocks' not in globals() or df_found_stocks.empty:
        print("저장할 신규 데이터가 없습니다.")
        return

    df_new = df_found_stocks.copy()
    today_date = datetime.now().strftime('%Y-%m-%d')
    df_new.insert(0, 'Date', today_date)

    if os.path.exists(drive_path):
        df_existing = pd.read_excel(drive_path)
        # 오늘 날짜로 중복되는 데이터가 있다면 최신 데이터로 덮어쓰기 위해 keep='last'
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        df_final = df_final.drop_duplicates(subset=['Date', 'ticker'], keep='last')
    else:
        df_final = df_new

    # 전체 데이터의 Industry 정보 올바르게 일괄 수정 (KRX-DESC 활용)
    print("산업(Industry) 정보를 KRX-DESC 기준으로 정확하게 업데이트합니다...")
    try:
        df_krx_desc = fdr.StockListing('KRX-DESC')
        if 'Industry' in df_krx_desc.columns:
            industry_map = df_krx_desc.set_index('Code')['Industry'].to_dict()
            # 엑셀에서 불러올 때 숫자로 변환된 ticker(예: 660)를 000660으로 맞춰서 매핑
            df_final['Industry'] = df_final['ticker'].apply(lambda x: str(x).zfill(6)).map(industry_map).fillna('Unknown')
        else:
            if 'Industry' not in df_final.columns:
                df_final['Industry'] = 'Unknown'
    except Exception as e:
        print(f"산업 정보 업데이트 실패: {e}")
        if 'Industry' not in df_final.columns:
            df_final['Industry'] = 'Unknown'

    df_final.to_excel(drive_path, index=False)
    print(f"✅ 정상 산업명이 반영된 파일 저장 완료: {drive_path}")

save_to_excel()

# ====================================================================
# #결과 분석
# ====================================================================

# ----- [cell 22] ----------------------------------------
import FinanceDataReader as fdr
import pandas as pd

# --- 1. 산업 정보 업데이트 (Sector 삭제) ---
print("Attempting to fetch detailed industry information using 'KRX-DESC'...")
try:
    # Initialize df_analysis with df_found_stocks if it exists, otherwise create an empty DataFrame.
    if 'df_found_stocks' in globals() and not df_found_stocks.empty:
        df_analysis = df_found_stocks.copy()
        df_analysis = df_analysis.rename(columns={'close': 'current_price'})
        df_analysis['first_price'] = df_analysis['current_price']
        df_analysis['return_pct'] = 0.0
        df_analysis['streak'] = 0
        df_analysis['frequency'] = 1
    else:
        df_analysis = pd.DataFrame(columns=['ticker', 'name', 'volume', 'current_price', 'amount', 'first_price', 'return_pct', 'streak', 'frequency'])
        print("Warning: df_found_stocks not found or empty. Initializing df_analysis as empty.")

    df_krx_desc = fdr.StockListing('KRX-DESC')

    if 'Industry' in df_krx_desc.columns:
        # 병합을 위한 산업 데이터 준비
        industry_map = df_krx_desc[['Code', 'Industry']].copy()
        industry_map['Code'] = industry_map['Code'].astype(str)
        df_analysis['ticker'] = df_analysis['ticker'].astype(str)

        # 이전에 생성된 'Sector' 또는 'Industry' 컬럼이 있으면 우선 제거
        cols_to_remove = [c for c in ['Sector', 'Industry'] if c in df_analysis.columns]
        if cols_to_remove:
            df_analysis = df_analysis.drop(columns=cols_to_remove, errors='ignore')

        # 새로운 산업 정보 병합
        df_analysis = pd.merge(df_analysis, industry_map, left_on='ticker', right_on='Code', how='left')

        # 남은 NaN 채우기
        df_analysis['Industry'] = df_analysis['Industry'].fillna('Unknown')

        # 불필요한 Code 컬럼 삭제
        if 'Code' in df_analysis.columns:
            df_analysis = df_analysis.drop(columns=['Code'])

        print("Industry information updated successfully.")
    else:
        print("'Industry' column not found in KRX-DESC. Keeping existing data.")

except Exception as e:
    print(f"Error fetching KRX-DESC or processing data: {e}")

# 확인을 위해 처음 몇 행과 산업(Industry) 카운트 표시
if not df_analysis.empty:
    display(df_analysis.head())
    print("\nIndustry Value Counts:")
    print(df_analysis['Industry'].value_counts().head(10))
else:
    print("df_analysis is empty after processing.")

# ----- [cell 23] ----------------------------------------
import FinanceDataReader as fdr
import pandas as pd

print("=== FinanceDataReader 연결 테스트 ===")

# 1. 미국 시장 데이터 (S&P 500) 테스트 - 외부 서버 연결 확인
try:
    sp500 = fdr.DataReader('US500', '2024-01-01')
    print(f"✅ [성공] S&P 500 데이터 수신 완료 (최근 종가: {sp500['Close'].iloc[-1]:,.2f})")
except Exception as e:
    print(f"❌ [실패] 외부 데이터 수신 오류: {e}")

# 2. KRX 종목 리스트 테스트 - 404 에러 발생 여부 확인
try:
    print("\nKRX 종목 리스트를 불러오는 중...")
    df_krx = fdr.StockListing('KRX')
    print(f"✅ [성공] KRX 종목 리스트 수신 완료 (총 {len(df_krx)}개 종목)")
    display(df_krx.head())
except Exception as e:
    print(f"❌ [실패] KRX 리스트 수신 오류: {e}")
    print("\n💡 안내: 현재 fdr.StockListing('KRX')에서 404 에러가 발생한다면, 라이브러리가 참조하는 KRX 서버 주소가 변경되었을 가능성이 매우 높습니다.")
    print("이 경우 앞서 제안드린 'pykrx' 라이브러리를 대안으로 사용하는 것이 가장 안정적입니다.")

# ----- [cell 24] ----------------------------------------
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import os
# [로컬] google.colab drive 제거

# --- 구글 드라이브의 파일 경로로 수정 ---
drive_path = '종목탐색_TOP30.xlsx'  # [로컬] 현재 폴더의 엑셀

# 드라이브 마운트 확인 및 로드
try:
    # (로컬) 드라이브 마운트 불필요

    if os.path.exists(drive_path):
        df_history = pd.read_excel(drive_path)
        # 날짜 형식 변환
        df_history['Date'] = pd.to_datetime(df_history['Date'])

        # 최신 날짜 확인
        latest_date = df_history['Date'].max()
        print(f"분석 기준 날짜 (Google Drive): {latest_date.strftime('%Y-%m-%d')}")

        # 최신 날짜의 데이터만 추출 (오늘의 포착 종목)
        df_today = df_history[df_history['Date'] == latest_date].copy()

        if df_today.empty:
            print("최신 날짜의 데이터가 없습니다.")
        else:
            # --- 1. Streak (연속 등장) 계산 ---
            df_sorted = df_history.sort_values(by=['ticker', 'Date'], ascending=[True, False])
            streaks = {}
            available_dates = sorted(df_history['Date'].unique(), reverse=True)

            for ticker in df_today['ticker'].unique():
                streak = 0
                dates_appeared = set(df_sorted[df_sorted['ticker'] == ticker]['Date'])
                for i, date in enumerate(available_dates):
                    if date in dates_appeared:
                        streak += 1
                    else:
                        if i == 0: continue
                        break
                streaks[ticker] = streak

            df_today['Streak'] = df_today['ticker'].map(streaks)

            # --- 2. 시각화 ---
            plt.figure(figsize=(20, 10))

            # (1) 산업 비중 (Pie Chart)
            plt.subplot(1, 2, 1)
            if 'Industry' not in df_today.columns:
                 df_today['Industry'] = 'Unknown'

            industry_counts = df_today['Industry'].value_counts()
            if len(industry_counts) > 10:
                main_industries = industry_counts.head(9)
                others_count = industry_counts.iloc[9:].sum()
                industry_counts = pd.concat([main_industries, pd.Series([others_count], index=['Others'])])

            colors = sns.color_palette('pastel')[0:len(industry_counts)]
            plt.pie(industry_counts, labels=industry_counts.index, autopct='%1.1f%%', startangle=140, colors=colors)
            plt.title(f'Industry Distribution ({latest_date.strftime("%Y-%m-%d")})', fontsize=15)

            # (2) 연속 등장 종목 (Bar Chart)
            plt.subplot(1, 2, 2)
            consecutive_stocks = df_today[df_today['Streak'] > 1].sort_values(by='Streak', ascending=False).head(15)
            if not consecutive_stocks.empty:
                sns.barplot(data=consecutive_stocks, x='Streak', y='name', hue='name', palette='magma', legend=False)
                plt.title('Top Consecutive Stocks (Streak > 1)', fontsize=15)
                plt.xlabel('Consecutive Days')
                plt.ylabel('Stock Name')
            else:
                plt.text(0.5, 0.5, 'No Consecutive Stocks Found', ha='center', va='center', fontsize=12)
                plt.title('Consecutive Stocks')

            plt.tight_layout()
            plt.show()

            # --- 3. 데이터프레임 출력 ---
            cols_base = ['ticker', 'name', 'close', 'amount', 'change_ratio', 'Industry']
            if 'close' not in df_today.columns and 'current_price' in df_today.columns:
                df_today = df_today.rename(columns={'current_price': 'close'})

            new_stocks = df_today[df_today['Streak'] == 1]
            print(f"\n[✨ 오늘 신규 진입 종목] (총 {len(new_stocks)}개)")
            if not new_stocks.empty:
                display(new_stocks[[c for c in cols_base if c in new_stocks.columns]].reset_index(drop=True))

            consecutive_stocks_all = df_today[df_today['Streak'] > 1].sort_values(by='Streak', ascending=False)
            print(f"\n[🔥 연속 등장 종목] (총 {len(consecutive_stocks_all)}개)")
            if not consecutive_stocks_all.empty:
                cols_cons = ['ticker', 'name', 'Streak', 'close', 'amount', 'change_ratio', 'Industry']
                display(consecutive_stocks_all[[c for c in cols_cons if c in consecutive_stocks_all.columns]].reset_index(drop=True))
    else:
        print(f"구글 드라이브에서 파일을 찾을 수 없습니다: {drive_path}")

except Exception as e:
    print(f"오류 발생: {e}")

# ====================================================================
# # 시장 건전성 확인
# 
# 주도주 매매 진입 전 **거시 환경 적합성**을 판단하는 4-Pillar 시스템
# - **Pillar 1. Liquidity (25%)**: 전체 거래대금 추세, 외국인 수급
# - **Pillar 2. Breadth (35%)**: 신고가/신저가 비율, A/D, 200일선 위 종목 비율
# - **Pillar 3. Leadership (25%)**: 시총 Top50 비중, 저가주(밈주식) 과열도, Top10 거래대금
# - **Pillar 4. Risk (15%)**: VKOSPI 패닉 게이지
# 
# → 종합 점수 0~100 → 진입 비중 자동 차등 (RISK-ON 100% / NEUTRAL 50% / CAUTIOUS 20% / RISK-OFF 0%)
# ====================================================================

# ----- [cell 27] ----------------------------------------
# import pandas as pd
# import numpy as np
# import FinanceDataReader as fdr
# from datetime import datetime, timedelta
# import time
# import json

# """
# Market Health Monitor v1.4 (Trading-Ready)
# - 원본 4영역 구조(liquidity / breadth / leadership / risk) 유지
# - breadth: A/D 비율 + 신고가·신저가 비율(52주) + 누적 A/D 라인 + McClellan
# - risk:    VKOSPI 수준 + Z-score + KOSPI HV20 + 볼린저 밴드폭
# - 매매 연계: calculate_health_score() / report(send_to_slack, qt)
# - 종합점수 → regime + position_pct 자동 매핑
# """


# class MarketHealthMonitor:
#     WEIGHTS = {
#         'liquidity':  0.25,
#         'breadth':    0.25,
#         'leadership': 0.25,
#         'risk':       0.25,
#     }

#     LOOKBACK_52W = 252  # 52주 신고가/신저가 기준 영업일

#     # 종합점수 → (국면 라벨, 권장 진입 비중) 매핑 테이블
#     REGIME_TABLE = [
#         # (최소점수, 라벨, position_pct)
#         (75, "🟢 STRONG BULL (RISK-ON)",     1.00),
#         (65, "🟢 BULL (적극 매수)",            0.75),
#         (55, "🟡 NEUTRAL-PLUS (선택적 매수)", 0.50),
#         (45, "⚪ NEUTRAL (관망)",              0.30),
#         (35, "🟠 NEUTRAL-MINUS (방어)",       0.15),
#         (0,  "🔴 RISK-OFF (현금/헷지)",       0.00),
#     ]

#     def __init__(self, base_date=None, lookback_days=420):
#         if base_date is None:
#             self.base_dt = datetime.now()
#         else:
#             self.base_dt = datetime.strptime(base_date, '%Y%m%d')

#         self.base_date_str = self.base_dt.strftime('%Y-%m-%d')
#         self.start_date_str = (self.base_dt - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

#         self.scores = {}
#         self.signals = {}
#         self.metrics = {}
#         self.raw_data = {}

#         self.final_score = None
#         self.regime = None
#         self.position_pct = None

#     # ------------------------------------------------------------------
#     # 1) Liquidity
#     # ------------------------------------------------------------------
#     def evaluate_liquidity(self):
#         signals = []
#         score = 50

#         try:
#             df_kospi = fdr.DataReader('KS11', self.start_date_str, self.base_date_str)
#             df_kosdaq = fdr.DataReader('KQ11', self.start_date_str, self.base_date_str)

#             liq_series = (df_kospi['Close'] * df_kospi['Volume'] +
#                           df_kosdaq['Close'] * df_kosdaq['Volume']) / 1e9
#             ma20 = liq_series.rolling(20).mean().iloc[-1]
#             ma60 = liq_series.rolling(60).mean().iloc[-1]

#             liq_ratio = ma20 / ma60
#             if liq_ratio >= 1.10:
#                 score += 20
#                 signals.append(f"✅ 거래대금 추세 강세 (20/60MA: {liq_ratio:.2f})")
#             elif liq_ratio < 0.9:
#                 score -= 20
#                 signals.append(f"🚨 거래대금 위축 (20/60MA: {liq_ratio:.2f})")
#             else:
#                 signals.append(f"➖ 거래대금 보통 (20/60MA: {liq_ratio:.2f})")

#             df_usd = fdr.DataReader('USD/KRW', self.start_date_str, self.base_date_str)
#             usd_current = df_usd['Close'].iloc[-1]
#             usd_ma20 = df_usd['Close'].rolling(20).mean().iloc[-1]

#             if usd_current < usd_ma20:
#                 score += 10
#                 signals.append(f"✅ 환율 안정화 구간 (외인 수급 우호적)")
#             else:
#                 score -= 10
#                 signals.append(f"⚠️ 환율 상승 압력 (외인 이탈 주의)")
#         except Exception as e:
#             signals.append(f"⚠️ 유동성 분석 중 오류: {e}")

#         self.scores['liquidity'] = max(0, min(100, score))
#         self.signals['liquidity'] = signals
#         return self.scores['liquidity']

#     # ------------------------------------------------------------------
#     # 2) Breadth — A/D + NH/NL + 누적 A/D + McClellan
#     # ------------------------------------------------------------------
#     def evaluate_breadth(self):
#         signals = []
#         score = 50
#         metrics = {}

#         try:
#             df_all = fdr.StockListing('KRX')
#             df_all = df_all[~df_all['Name'].astype(str).str.contains(
#                 r'스팩|우$|우B|ETF|ETN', na=False, regex=True
#             )]

#             # ---------- (A) 당일 A/D 비율 ----------
#             chg_col = None
#             for col in ['ChgRate', 'ChangesRatio', 'ChangeRatio']:
#                 if col in df_all.columns:
#                     chg_col = col
#                     break

#             if chg_col:
#                 advances = (df_all[chg_col] > 0).sum()
#                 declines = (df_all[chg_col] < 0).sum()
#                 ad_ratio = advances / max(declines, 1)
#                 metrics['ad_ratio_today'] = round(ad_ratio, 2)

#                 if ad_ratio >= 1.5:
#                     score += 10
#                     signals.append(f"✅ 당일 상승 종목 우세 (A/D: {ad_ratio:.2f})")
#                 elif ad_ratio < 0.7:
#                     score -= 10
#                     signals.append(f"🚨 당일 하락 종목 압도 (A/D: {ad_ratio:.2f})")
#                 else:
#                     signals.append(f"➖ 당일 A/D 중립 ({ad_ratio:.2f})")
#             else:
#                 signals.append("⚠️ 등락률 데이터를 찾을 수 없어 당일 A/D 분석 건너뜀")

#             # ---------- (B) 시총 상위 100종목 가격 패널 ----------
#             top100 = df_all.nlargest(100, 'Marcap')
#             close_panel, ret_panel = {}, {}
#             above_count = 0

#             for code in top100['Code']:
#                 try:
#                     df = fdr.DataReader(code, self.start_date_str, self.base_date_str)
#                     if df.empty:
#                         continue
#                     close_panel[code] = df['Close']
#                     ret_panel[code] = df['Close'].pct_change()
#                     if df['Close'].iloc[-1] > df['Close'].rolling(200).mean().iloc[-1]:
#                         above_count += 1
#                 except Exception:
#                     continue

#             close_df = pd.DataFrame(close_panel).sort_index()
#             ret_df = pd.DataFrame(ret_panel).sort_index()
#             metrics['universe_collected'] = int(close_df.shape[1])

#             # (B-1) 200일선 위 종목 비율
#             pct_above = (above_count / max(len(top100), 1)) * 100
#             metrics['pct_above_ma200'] = round(pct_above, 1)
#             if pct_above >= 60:
#                 score += 10
#                 signals.append(f"✅ 주도주 정배열 강화 ({pct_above:.0f}%)")
#             elif pct_above < 40:
#                 score -= 10
#                 signals.append(f"🚨 시장 에너지 약화 ({pct_above:.0f}%)")
#             else:
#                 signals.append(f"➖ 200일선 위 종목 비율 {pct_above:.0f}%")

#             # (B-2) 52주 NH/NL
#             window = self.LOOKBACK_52W
#             if len(close_df) >= window // 2 and not close_df.empty:
#                 roll_max = close_df.rolling(window, min_periods=window // 2).max()
#                 roll_min = close_df.rolling(window, min_periods=window // 2).min()

#                 nh_series = (close_df == roll_max).sum(axis=1)
#                 nl_series = (close_df == roll_min).sum(axis=1)

#                 today_nh = int(nh_series.iloc[-1])
#                 today_nl = int(nl_series.iloc[-1])
#                 nh_nl = today_nh / max(today_nl, 1)

#                 nhnl_cum = (nh_series - nl_series).cumsum()
#                 cum_slope_20 = (int(nhnl_cum.iloc[-1] - nhnl_cum.iloc[-21])
#                                 if len(nhnl_cum) > 21 else 0)

#                 metrics.update({
#                     'new_highs_52w': today_nh,
#                     'new_lows_52w': today_nl,
#                     'nh_nl_ratio': round(nh_nl, 2),
#                     'nhnl_cum_slope_20d': cum_slope_20,
#                 })

#                 if nh_nl >= 3:
#                     score += 10
#                     signals.append(f"✅ 52주 신고가 우위 강력 (NH:{today_nh}/NL:{today_nl}, 비율 {nh_nl:.1f})")
#                 elif nh_nl >= 1.5:
#                     score += 5
#                     signals.append(f"➕ 52주 신고가 우위 (NH:{today_nh}/NL:{today_nl}, 비율 {nh_nl:.2f})")
#                 elif nh_nl <= 0.33:
#                     score -= 10
#                     signals.append(f"🚨 52주 신저가 압도 (NH:{today_nh}/NL:{today_nl}, 비율 {nh_nl:.2f})")
#                 elif nh_nl <= 0.7:
#                     score -= 5
#                     signals.append(f"⚠️ 52주 신저가 우세 (NH:{today_nh}/NL:{today_nl}, 비율 {nh_nl:.2f})")
#                 else:
#                     signals.append(f"➖ NH/NL 균형 (NH:{today_nh}/NL:{today_nl}, 비율 {nh_nl:.2f})")

#                 if cum_slope_20 > 0:
#                     score += 5
#                     signals.append(f"✅ NH-NL 누적선 상승 추세 (20일 변화 +{cum_slope_20})")
#                 elif cum_slope_20 < 0:
#                     score -= 5
#                     signals.append(f"⚠️ NH-NL 누적선 하락 추세 (20일 변화 {cum_slope_20})")
#             else:
#                 signals.append("⚠️ NH/NL 산출에 충분한 데이터가 없습니다")

#             # (B-3) 누적 A/D 라인 + McClellan
#             if not ret_df.empty and len(ret_df) >= 60:
#                 adv_s = (ret_df > 0).sum(axis=1)
#                 dec_s = (ret_df < 0).sum(axis=1)

#                 ad_line = (adv_s - dec_s).cumsum()
#                 ad_ma20 = ad_line.rolling(20).mean()
#                 ad_ma60 = ad_line.rolling(60).mean()

#                 ad_up = ad_line.iloc[-1] > ad_ma20.iloc[-1] > ad_ma60.iloc[-1]
#                 ad_down = ad_line.iloc[-1] < ad_ma20.iloc[-1] < ad_ma60.iloc[-1]
#                 metrics['ad_line_trend'] = 'up' if ad_up else ('down' if ad_down else 'mixed')

#                 if ad_up:
#                     score += 5
#                     signals.append("✅ 누적 A/D 라인 정배열 (시장 폭 양호)")
#                 elif ad_down:
#                     score -= 5
#                     signals.append("🚨 누적 A/D 라인 역배열 (시장 폭 약화)")

#                 ad_pct = (adv_s - dec_s) / (adv_s + dec_s).replace(0, np.nan)
#                 ema19 = ad_pct.ewm(span=19, adjust=False).mean()
#                 ema39 = ad_pct.ewm(span=39, adjust=False).mean()
#                 mcc = float((ema19.iloc[-1] - ema39.iloc[-1]) * 1000)
#                 metrics['mcclellan'] = round(mcc, 1)

#                 if mcc >= 50:
#                     score += 5
#                     signals.append(f"✅ McClellan 강세 ({mcc:.1f})")
#                 elif mcc <= -50:
#                     score -= 5
#                     signals.append(f"🚨 McClellan 약세 ({mcc:.1f})")
#                 else:
#                     signals.append(f"➖ McClellan 중립 ({mcc:.1f})")

#         except Exception as e:
#             signals.append(f"⚠️ 시장 폭 분석 중 오류: {e}")

#         self.scores['breadth'] = max(0, min(100, score))
#         self.signals['breadth'] = signals
#         self.metrics['breadth'] = metrics
#         return self.scores['breadth']

#     # ------------------------------------------------------------------
#     # 3) Leadership
#     # ------------------------------------------------------------------
#     def evaluate_leadership(self):
#         signals = []
#         score = 50
#         metrics = {}
#         try:
#             df_all = fdr.StockListing('KRX')
#             total_marcap = df_all['Marcap'].sum()
#             top10_marcap = df_all.nlargest(10, 'Marcap')['Marcap'].sum()
#             concentration = (top10_marcap / total_marcap) * 100
#             metrics['top10_concentration_pct'] = round(concentration, 2)

#             if 30 <= concentration <= 45:
#                 score += 15
#                 signals.append(f"✅ 대형주 리더십 건전 ({concentration:.1f}%)")
#             else:
#                 score -= 10
#                 signals.append(f"⚠️ 리더십 쏠림 또는 부재 ({concentration:.1f}%)")
#         except Exception as e:
#             signals.append(f"⚠️ 리더십 분석 중 오류: {e}")

#         self.scores['leadership'] = max(0, min(100, score))
#         self.signals['leadership'] = signals
#         self.metrics['leadership'] = metrics
#         return self.scores['leadership']

#     # ------------------------------------------------------------------
#     # 4) Risk — VKOSPI 수준 + Z-score + HV20 + 볼린저 밴드폭
#     # ------------------------------------------------------------------
#     def evaluate_risk(self):
#         signals = []
#         score = 50
#         metrics = {}

#         try:
#             df_vix = fdr.DataReader('KS200VIX', self.start_date_str, self.base_date_str)
#             vix_series = df_vix['Close'].dropna()
#             vix = float(vix_series.iloc[-1])
#             metrics['vkospi'] = round(vix, 2)

#             if vix <= 15:
#                 score += 20
#                 signals.append(f"✅ 저변동성 안정 구간 ({vix:.1f})")
#             elif vix >= 25:
#                 score -= 25
#                 signals.append(f"🚨 고변동성 위험 구간 ({vix:.1f})")
#             else:
#                 signals.append(f"➖ 변동성 보통 수준 ({vix:.1f})")

#             if len(vix_series) >= 60:
#                 vix_mean = float(vix_series.rolling(60).mean().iloc[-1])
#                 vix_std = float(vix_series.rolling(60).std().iloc[-1])
#                 vix_z = (vix - vix_mean) / vix_std if vix_std > 0 else 0
#                 metrics['vkospi_z60'] = round(vix_z, 2)

#                 if vix_z >= 1.5:
#                     score -= 10
#                     signals.append(f"⚠️ VKOSPI Z-score 급등 ({vix_z:+.2f}σ)")
#                 elif vix_z <= -1:
#                     score += 5
#                     signals.append(f"✅ VKOSPI Z-score 안정 ({vix_z:+.2f}σ)")
#                 else:
#                     signals.append(f"➖ VKOSPI Z-score 중립 ({vix_z:+.2f}σ)")
#         except Exception:
#             signals.append("⚠️ 변동성 지수(KS200VIX) 데이터 수집 실패")

#         try:
#             df_kospi = fdr.DataReader('KS11', self.start_date_str, self.base_date_str)
#             close = df_kospi['Close'].dropna()

#             log_ret = np.log(close / close.shift(1))
#             hv20 = float(log_ret.rolling(20).std().iloc[-1] * np.sqrt(252) * 100)
#             metrics['kospi_hv20'] = round(hv20, 2)

#             if hv20 < 12:
#                 score += 5
#                 signals.append(f"✅ KOSPI HV20 낮음 ({hv20:.1f}%) — 추세 안정")
#             elif hv20 > 22:
#                 score -= 10
#                 signals.append(f"⚠️ KOSPI HV20 높음 ({hv20:.1f}%) — 변동성 확장")
#             else:
#                 signals.append(f"➖ KOSPI HV20 보통 ({hv20:.1f}%)")

#             ma20 = close.rolling(20).mean()
#             sd20 = close.rolling(20).std()
#             if not pd.isna(ma20.iloc[-1]) and ma20.iloc[-1] != 0:
#                 upper = ma20.iloc[-1] + 2 * sd20.iloc[-1]
#                 lower = ma20.iloc[-1] - 2 * sd20.iloc[-1]
#                 bbw = float((upper - lower) / ma20.iloc[-1] * 100)
#                 metrics['bb_width_pct'] = round(bbw, 2)

#                 if bbw < 3:
#                     signals.append(f"🔍 볼린저 밴드폭 수축 ({bbw:.2f}%) — 변동성 폭발 임박 주의")
#                 elif bbw > 8:
#                     signals.append(f"🔥 볼린저 밴드폭 확장 ({bbw:.2f}%) — 추세 전환 진행")
#                 else:
#                     signals.append(f"➖ 볼린저 밴드폭 보통 ({bbw:.2f}%)")
#         except Exception as e:
#             signals.append(f"⚠️ KOSPI 변동성 보조지표 분석 오류: {e}")

#         self.scores['risk'] = max(0, min(100, score))
#         self.signals['risk'] = signals
#         self.metrics['risk'] = metrics

#         return self.scores['risk']

#     # ------------------------------------------------------------------
#     # 종합 점수 계산 + regime/position_pct 매핑
#     # ------------------------------------------------------------------
#     def calculate_health_score(self):
#         """모든 영역을 평가하고 종합점수·국면·진입비중을 산출. 출력은 하지 않는다."""
#         self.evaluate_liquidity()
#         self.evaluate_breadth()
#         self.evaluate_leadership()
#         self.evaluate_risk()

#         self.final_score = sum(
#             self.scores.get(k, 50) * w for k, w in self.WEIGHTS.items()
#         )
#         self.regime, self.position_pct = self._map_regime(self.final_score)
#         return self.final_score

#     def _map_regime(self, score):
#         for threshold, label, pct in self.REGIME_TABLE:
#             if score >= threshold:
#                 return label, pct
#         return self.REGIME_TABLE[-1][1], self.REGIME_TABLE[-1][2]

#     # 하위 호환용 별칭
#     def calculate_and_report(self):
#         self.calculate_health_score()
#         return self.report()

#     # ------------------------------------------------------------------
#     # 리포트 출력 + Slack 전송 + 결과 dict 반환
#     # ------------------------------------------------------------------
#     def report(self, send_to_slack=False, qt=None):
#         if self.final_score is None:
#             self.calculate_health_score()

#         text = self._build_report_text()
#         print(text)

#         if send_to_slack:
#             self._send_to_slack(text, qt)

#         return {
#             'base_date':    self.base_date_str,
#             'final_score':  round(self.final_score, 2),
#             'regime':       self.regime,
#             'position_pct': self.position_pct,
#             'scores':       dict(self.scores),
#             'signals':      {k: list(v) for k, v in self.signals.items()},
#             'metrics':      {k: dict(v) for k, v in self.metrics.items()},
#         }

#     def _build_report_text(self):
#         lines = []
#         lines.append("=" * 60)
#         lines.append(f"📊 MARKET HEALTH REPORT ({self.base_date_str})")
#         lines.append("=" * 60)
#         lines.append(f"🎯 종합 점수: {self.final_score:.1f} / 100")
#         lines.append(f"📌 국면 판단: {self.regime}")
#         lines.append(f"💰 권장 진입 비중: {self.position_pct * 100:.0f}%")
#         lines.append("")
#         lines.append("[ 영역별 점수 ]")
#         for k, w in self.WEIGHTS.items():
#             lines.append(f"  - {k.upper():12} : {self.scores.get(k, 0):>5.1f}  (가중치 {int(w*100)}%)")

#         lines.append("")
#         lines.append("[ 핵심 지표 ]")
#         for area, m in self.metrics.items():
#             if not m:
#                 continue
#             lines.append(f"  · {area.upper()}")
#             for k, v in m.items():
#                 lines.append(f"      {k:<22} {v}")

#         lines.append("")
#         lines.append("[ 세부 시그널 ]")
#         for area in ['liquidity', 'breadth', 'leadership', 'risk']:
#             lines.append(f"  ◆ {area.upper()}")
#             for s in self.signals.get(area, []):
#                 lines.append(f"    {s}")

#         return "\n".join(lines)

#     # ------------------------------------------------------------------
#     # Slack 전송 (qt 객체 형태에 따라 자동 판별)
#     # ------------------------------------------------------------------
#     def _send_to_slack(self, text, qt):
#         """qt는 다음 중 하나를 받을 수 있다.
#            1) send_message(text) 메서드를 가진 객체
#            2) post_message(text) 메서드를 가진 객체
#            3) slack_sdk.WebClient (chat_postMessage)
#            4) Slack Incoming Webhook URL 문자열
#            5) None  → 콘솔만 안내
#         """
#         if qt is None:
#             print("⚠️ Slack 전송 요청되었으나 qt 객체가 없습니다 (콘솔 출력만 수행)")
#             return

#         try:
#             if hasattr(qt, 'send_message'):
#                 qt.send_message(text)
#             elif hasattr(qt, 'post_message'):
#                 qt.post_message(text)
#             elif hasattr(qt, 'chat_postMessage'):
#                 channel = getattr(qt, 'default_channel', None) or '#general'
#                 qt.chat_postMessage(channel=channel, text=text)
#             elif isinstance(qt, str) and qt.startswith('http'):
#                 import urllib.request
#                 req = urllib.request.Request(
#                     qt,
#                     data=json.dumps({'text': text}).encode('utf-8'),
#                     headers={'Content-Type': 'application/json'},
#                 )
#                 urllib.request.urlopen(req, timeout=5)
#             else:
#                 print(f"⚠️ qt 형식을 인식하지 못했습니다: {type(qt)}")
#                 return
#             print("📤 Slack 전송 완료")
#         except Exception as e:
#             print(f"⚠️ Slack 전송 실패: {e}")


# # ----------------------------------------------------------------------
# # === 시장 건전성 평가 실행 (테스트 셀) ===
# # ----------------------------------------------------------------------
# if __name__ == '__main__':
#     # qt 객체가 환경에 정의되어 있지 않으면 None으로 처리
#     try:
#         qt  # noqa: F821
#     except NameError:
#         qt = None

#     monitor = MarketHealthMonitor()
#     monitor.calculate_health_score()
#     health_result = monitor.report(send_to_slack=True, qt=qt)

#     # 진입 비중 추출
#     MARKET_POSITION_PCT = health_result['position_pct']
#     MARKET_REGIME = health_result['regime']

#     print(f"\n\n{'=' * 60}")
#     print(f"🎯 본 분석 결과로 주도주 진입 비중을 자동 조정합니다")
#     print(f"{'=' * 60}")
#     print(f"국면: {MARKET_REGIME}")
#     print(f"권장 비중: {MARKET_POSITION_PCT * 100:.0f}%")

#     # 차후 매매 로직 예시
#     # 종목당_매수금액 = 기본금액 * MARKET_POSITION_PCT
