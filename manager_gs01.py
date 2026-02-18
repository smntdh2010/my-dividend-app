import streamlit as st
import pandas as pd
import yfinance as yf
import holidays
import io
from datetime import datetime
from pandas.tseries.offsets import CustomBusinessDay
from streamlit_gsheets import GSheetsConnection

# --- 웹 페이지 설정 ---
st.set_page_config(page_title="미국주식 통합 배당 관리 (Cloud)", layout="wide")

# --- CSS 스타일 설정 ---
st.markdown("""
    <style>
    .stDataFrame div[data-testid="stTable"] { font-size: 12px; }
    .block-container { padding-top: 2rem; padding-bottom: 0rem; }
    </style>
    """, unsafe_allow_html=True)




# --- 구글 시트 연결 및 배당 계산 클래스 ---
class DividendDashboard:

    def check_password():
        """비밀번호가 맞는지 확인하는 함수"""
        if "password_correct" not in st.session_state:
            st.session_state["password_correct"] = False

        if not st.session_state["password_correct"]:
            # 비밀번호 입력창 표시
            pwd = st.text_input("접근 비밀번호를 입력하세요", type="password")
            if st.button("로그인"):
                if pwd == "9705": # 여기에 실제 사용할 비번 입력
                    st.session_state["password_correct"] = True
                    st.rerun()
                else:
                    st.error("비밀번호가 틀렸습니다.")
            return False
        return True

    def __init__(self):
        self.tax_rate = 0.15
        self.kr_biz_day = CustomBusinessDay(holidays=holidays.KR())
        self.us_biz_day = CustomBusinessDay(holidays=holidays.US())
        # 구글 시트 연결 초기화
        self.conn = st.connection("gsheets", type=GSheetsConnection)

    def load_assets(self):
        """구글 시트에서 자산 내역 로드 및 정렬"""
        # ttl=0은 캐시를 사용하지 않고 항상 최신 데이터를 가져옴을 의미
        df = self.conn.read(ttl=0)
        if df is not None and not df.empty:
            df['매수일'] = pd.to_datetime(df['매수일']).dt.date
            # 종목코드, 매수일, 계좌번호 순 정렬
            df = df.sort_values(by=['종목코드', '매수일', '계좌번호'], ascending=[True, True, True])
        return df

    def save_assets(self, df):
        """수정된 데이터를 구글 시트에 다시 쓰기"""
        self.conn.update(data=df)
        st.cache_data.clear() # 캐시 초기화

    def get_exchange_rate(self, target_date_str):
        try:
            ticker = yf.Ticker("USDKRW=X")
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            hist = ticker.history(start=target_date, end=target_date + pd.Timedelta(days=5))
            return round(hist['Close'].iloc[0], 2) if not hist.empty else 1350.0
        except:
            return 1350.0

    def fetch_data_by_year(self, target_year, balance_df):
        all_data = []
        unique_tickers = balance_df['종목코드'].unique()
        progress_bar = st.progress(0)
        target_year_int = int(target_year)

        for idx, ticker_symbol in enumerate(unique_tickers):
            ticker_symbol = ticker_symbol.strip().upper()
            stock = yf.Ticker(ticker_symbol)
            div_history = stock.dividends
            if div_history.empty: continue
            
            div_history.index = div_history.index.tz_localize(None)
            search_range = div_history[(div_history.index.year >= target_year_int - 1) & (div_history.index.year <= target_year_int)]
            history = stock.history(start=f"{target_year_int-1}-01-01", end=f"{target_year_int+1}-02-01")

            for ex_div_date, dps in search_range.items():
                ex_date = ex_div_date.date()
                prior_history = history[history.index.date < ex_date]
                actual_prev_close = prior_history['Close'].iloc[-1] if not prior_history.empty else 0.0

                pay_local_dt = pd.to_datetime(ex_date) + self.us_biz_day
                pay_kr_dt = pay_local_dt + self.kr_biz_day
                if pay_kr_dt.year != target_year_int: continue

                valid_holdings = balance_df[
                    (balance_df['종목코드'].str.upper() == ticker_symbol) & 
                    (pd.to_datetime(balance_df['매수일']).dt.date <= ex_date)
                ]
                if valid_holdings.empty: continue
                
                for acc_no, group in valid_holdings.groupby('계좌번호'):
                    total_qty = group['수량'].sum()
                    ex_rate = self.get_exchange_rate(pay_kr_dt.strftime('%Y-%m-%d'))
                    all_data.append({
                        '배당락일': ex_date.strftime('%Y-%m-%d'),
                        '현지지급일': pay_local_dt.strftime('%Y-%m-%d'),
                        '국내지급일': pay_kr_dt.strftime('%Y-%m-%d'),
                        '종목코드': ticker_symbol, '수량': int(total_qty), '종가': float(actual_prev_close),
                        '배당률(%)': float((dps/actual_prev_close)*100) if actual_prev_close > 0 else 0,
                        '배당금': float(dps), '세전(USD)': float(total_qty * dps),
                        '세후(USD)': float((total_qty * dps) * (1 - self.tax_rate)),
                        '세전(원)': int((total_qty * dps) * ex_rate),
                        '세후(원)': int(((total_qty * dps) * (1 - self.tax_rate)) * ex_rate),
                        '환율': float(ex_rate), '계좌번호': str(acc_no)
                    })
            progress_bar.progress((idx + 1) / len(unique_tickers))
        progress_bar.empty()
        return pd.DataFrame(all_data)

# --- 앱 UI 실행부 ---
    if check_password():
        
        manager = DividendDashboard()
        tab1, tab2 = st.tabs(["📊 배당금 통합 리포트", "⚙️ 계좌/자산 관리"])

        with tab2:
            st.subheader("보유 종목 관리 (Google Sheets 동기화)")
            try:
                current_assets = manager.load_assets()
                edited_df = st.data_editor(
                    current_assets,
                    column_config={
                        "매수일": st.column_config.DateColumn("매수일", format="YYYY-MM-DD"),
                        "수량": st.column_config.NumberColumn("수량", min_value=1),
                    },
                    num_rows="dynamic", use_container_width=True, hide_index=True, height=1000, key="gsheet_editor"
                )
                if st.button("💾 구글 시트에 저장"):
                    manager.save_assets(edited_df)
                    st.success("구글 시트 데이터가 업데이트되었습니다!")
            except Exception as e:
                st.error(f"데이터를 불러오지 못했습니다. URL 설정을 확인하세요: {e}")

        with tab1:
            st.sidebar.header("조회 조건")
            target_year = st.sidebar.text_input("년도 (YYYY)", value=datetime.now().strftime('%Y'))

            if 'raw_data' not in st.session_state:
                st.session_state.raw_data = None

            if st.sidebar.button("배당 조회 실행"):
                balance_df = manager.load_assets()
                if balance_df.empty:
                    st.warning("데이터가 없습니다.")
                else:
                    with st.spinner("구글 시트에서 계산 중..."):
                        st.session_state.raw_data = manager.fetch_data_by_year(target_year, balance_df)

            if st.session_state.raw_data is not None and not st.session_state.raw_data.empty:
                raw_df = st.session_state.raw_data.copy()
                all_tickers = sorted(raw_df['종목코드'].unique())
                selected_tickers = st.multiselect("종목 필터", options=all_tickers)
                if selected_tickers:
                    raw_df = raw_df[raw_df['종목코드'].isin(selected_tickers)]

                if not raw_df.empty:
                    raw_df = raw_df.sort_values(by='국내지급일').reset_index(drop=True)
                    raw_df['pay_month'] = raw_df['국내지급일'].str[:7]
                    
                    final_list = []
                    prev_month_after_tax_usd = 0.0

                    for month, group in raw_df.groupby('pay_month', sort=False):
                        current_month_after_tax_usd = group['세후(USD)'].sum()
                        diff = current_month_after_tax_usd - prev_month_after_tax_usd
                        final_list.append(group)
                        sum_row = pd.DataFrame([{
                            '배당락일': f'[{month}] 합계', '세전(USD)': group['세전(USD)'].sum(), 
                            '세후(USD)': current_month_after_tax_usd, '세전(원)': group['세전(원)'].sum(), 
                            '세후(원)': group['세후(원)'].sum(), '환율': diff 
                        }])
                        final_list.append(sum_row)
                        prev_month_after_tax_usd = current_month_after_tax_usd
                        
                    display_df = pd.concat(final_list, ignore_index=True).drop(columns=['pay_month']).fillna("")
                    final_cols = ['배당락일', '현지지급일', '국내지급일', '종목코드', '수량', '종가', '배당률(%)', '배당금', '세전(USD)', '세후(USD)', '세전(원)', '세후(원)', '환율', '계좌번호']
                    display_df['계좌번호'] = display_df['계좌번호'].apply(lambda x: "*" * (len(str(x)) - 5) + str(x)[-5:] if (x and len(str(x)) > 5) else str(x))

                    def style_report(row):
                        styles = [''] * len(row)
                        is_sum_row = '합계' in str(row['배당락일'])
                        for i, col in enumerate(row.index):
                            if is_sum_row:
                                styles[i] = 'background-color: #FFEDD5; font-weight: bold;'
                                if col == '환율':
                                    val = row[col]
                                    if isinstance(val, (int, float)) and val < 0:
                                        styles[i] += 'color: #D32F2F; font-weight: bold;'# 진한 빨강 및 굵게
                                    elif isinstance(val, (int, float)) and val > 0:
                                        styles[i] += 'color: #009900; font-weight: bold;' # 양수일 경우 
                        return styles

                    fi, f2, f4 = lambda x: f"{x:,.0f}" if isinstance(x, (int, float)) else x, \
                                 lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else x, \
                                 lambda x: f"{x:,.4f}" if isinstance(x, (int, float)) else x

                    styled_df = display_df[final_cols].style \
                        .format({'수량': fi, '세전(원)': fi, '세후(원)': fi, '종가': f2, '배당률(%)': f2, '세전(USD)': f2, '세후(USD)': f2, '배당금': f4}) \
                        .format(lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else x, subset=['환율']) \
                        .apply(style_report, axis=1)

                    st.dataframe(styled_df, use_container_width=True, height=1000, hide_index=True)
                    
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        display_df[final_cols].to_excel(writer, index=False)
                    st.download_button("📥 엑셀 저장", buffer.getvalue(), f"Dividend_{target_year}.xlsx")
