import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests

# 1. 모바일 및 PC 화면 기본 설정
st.set_page_config(
    page_title="BTC 온체인 바닥 판독기",
    page_icon="₿",
    layout="wide"
)

st.title("₿ 비트코인 온체인 바닥 대시보드")
st.caption("조건: ① 현물가 < LTH 실현가 (저평가 구간)  |  ② STH-SOPR < 1.0 → 1.0 회복")

# 2. API 토큰 불러오기 (Streamlit Secrets 또는 사이드바 입력)
default_token = st.secrets.get("BGEOMETRICS_TOKEN", "") if hasattr(st, "secrets") else ""
with st.sidebar:
    st.header("⚙️ 설정")
    api_token = st.text_input("BGeometrics API 토큰", value=default_token, type="password")
    st.info("무료 API 호출 제한(일 15회) 보호를 위해 데이터는 6시간마다 자동 갱신됩니다.")

# 3. 실시간 BTC 현재가 조회 (Binance 공개 API)
@st.cache_data(ttl=60)
def get_live_btc_price():
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5).json()
        return float(res["price"])
    except Exception:
        return None

# 4. 온체인 시계열 데이터 조회 (6시간 캐싱)
@st.cache_data(ttl=21600)
def fetch_onchain_data(token):
    base_url = "https://bitcoin-data.com/v1"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    
    endpoints = {
        "btc_price": f"{base_url}/btc-price",
        "lth_rp": f"{base_url}/lth-realized-price",
        "sth_sopr": f"{base_url}/sth-sopr"
    }
    
    dfs = {}
    for key, url in endpoints.items():
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        raw = r.json()
        df = pd.DataFrame(raw)
        # 날짜 컬럼 표준화
        date_col = "d" if "d" in df.columns else df.columns[0]
        val_col = [c for c in df.columns if c not in ["d", "unixTs"]][0]
        df = df[[date_col, val_col]].rename(columns={date_col: "date", val_col: key})
        df["date"] = pd.to_datetime(df["date"])
        df[key] = pd.to_numeric(df[key], errors="coerce")
        dfs[key] = df

    merged = dfs["btc_price"].merge(dfs["lth_rp"], on="date", how="outer")
    merged = merged.merge(dfs["sth_sopr"], on="date", how="outer")
    merged = merged.sort_values("date").dropna(subset=["btc_price"]).reset_index(drop=True)
    merged["lth_rp"] = merged["lth_rp"].ffill()
    return merged

if not api_token:
    st.warning("왼쪽 사이드바(모바일은 왼쪽 상단 `>` 버튼)에 BGeometrics 무료 API 토큰을 입력해 주세요.")
    st.stop()

try:
    df = fetch_onchain_data(api_token)
    live_price = get_live_btc_price()
except Exception as e:
    st.error(f"데이터를 불러오는 중 오류가 발생했습니다. API 토큰이나 일일 호출 한도를 확인해 주세요.\n상세: {e}")
    st.stop()

# 최신 수치 계산
latest_lth = df["lth_rp"].dropna().iloc[-1]
current_btc = live_price if live_price else df["btc_price"].dropna().iloc[-1]
latest_sopr_series = df["sth_sopr"].dropna()
latest_sopr = latest_sopr_series.iloc[-1]
prev_sopr = latest_sopr_series.iloc[-2] if len(latest_sopr_series) > 1 else latest_sopr

gap_pct = ((current_btc - latest_lth) / latest_lth) * 100

# 5. 상단 핵심 요약 카드 (KPI)
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("현재 BTC 가격 (실시간)", f"${current_btc:,.0f}")
with col2:
    status_lth = "저평가 구간 진입!" if current_btc < latest_lth else "LTH 실현가 상회"
    st.metric("LTH 실현 가격", f"${latest_lth:,.0f}", f"{gap_pct:+.1f}% ({status_lth})",
              delta_color="inverse" if current_btc < latest_lth else "normal")
with col3:
    sopr_msg = "1.0 회복 시도/안착" if (prev_sopr < 1.0 and latest_sopr >= 1.0) else ("손실 매도 구간(<1.0)" if latest_sopr < 1.0 else "이익 구간(>1.0)")
    st.metric("최근 STH-SOPR", f"{latest_sopr:.3f}", sopr_msg)

# 종합 신호 배너
if current_btc < latest_lth and latest_sopr >= 1.0 and prev_sopr < 1.0:
    st.success("🟢 **[강력 바닥 변곡점 포착]** 현물가가 LTH 실현가 아래에 있으며, STH-SOPR이 1.0을 회복했습니다!")
elif current_btc < latest_lth:
    st.info("🟡 **[1단계 관심 구간]** 현물가가 LTH 실현가 아래(저평가 영역)에 진입했습니다. STH-SOPR 1.0 회복 여부를 주시하세요.")
else:
    st.write("⚪ **[대기 구간]** 현재 시장 가격이 LTH 실현 가격보다 위에 있습니다.")

# 6. 인터랙티브 시각화 차트 (상단: 가격 vs LTH 실현가 / 하단: STH-SOPR)
fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True,
    vertical_spacing=0.08, row_heights=[0.65, 0.35],
    subplot_titles=("BTC 시장 가격 vs LTH 실현 가격 (USD)", "단기보유자 수익성 지표 (STH-SOPR)")
)

# BTC 가격선
fig.add_trace(go.Scatter(
    x=df["date"], y=df["btc_price"], name="BTC 시장 가격",
    line=dict(color="#F7931A", width=2)
), row=1, col=1)

# LTH 실현가격선
fig.add_trace(go.Scatter(
    x=df["date"], y=df["lth_rp"], name="LTH 실현 가격",
    line=dict(color="#00D2FF", width=2)
), row=1, col=1)

# STH-SOPR 선 및 1.0 기준선
fig.add_trace(go.Scatter(
    x=df["date"], y=df["sth_sopr"], name="STH-SOPR",
    line=dict(color="#A855F7", width=1.8)
), row=2, col=1)

fig.add_hline(y=1.0, line_dash="dash", line_color="#FF4D4D", annotation_text="손익분기(1.0)", row=2, col=1)

fig.update_layout(
    height=620,
    template="plotly_dark",
    hovermode="x unified",
    margin=dict(l=10, r=10, t=40, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)
fig.update_yaxes(type="log", row=1, col=1)

st.plotly_chart(fig, use_container_width=True)

# 7. 하단 최근 데이터 요약 테이블
st.subheader("📋 최근 온체인 데이터 요약 (최근 14일)")
table_df = df.dropna(subset=["sth_sopr"]).tail(14).sort_values("date", ascending=False).copy()
table_df["날짜"] = table_df["date"].dt.strftime("%Y-%m-%d")
table_df["BTC 종가 ($)"] = table_df["btc_price"].map(lambda x: f"${x:,.0f}")
table_df["LTH 실현가 ($)"] = table_df["lth_rp"].map(lambda x: f"${x:,.0f}")
table_df["괴리율 (%)"] = ((table_df["btc_price"] - table_df["lth_rp"]) / table_df["lth_rp"] * 100).map(lambda x: f"{x:+.1f}%")
table_df["STH-SOPR"] = table_df["sth_sopr"].map(lambda x: f"{x:.3f}")
table_df["상태"] = table_df.apply(
    lambda r: "저평가(<LTH)" if r["btc_price"] < r["lth_rp"] else "일반", axis=1
)

st.dataframe(
    table_df[["날짜", "BTC 종가 ($)", "LTH 실현가 ($)", "괴리율 (%)", "STH-SOPR", "상태"]],
    use_container_width=True,
    hide_index=True
)
