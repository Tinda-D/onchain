import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from datetime import datetime, timezone

# 1. 페이지 기본 설정
st.set_page_config(
    page_title="BTC 온체인 바닥 판독기",
    page_icon="₿",
    layout="wide"
)

st.title("₿ 비트코인 온체인 바닥 대시보드")
st.caption("공간 조건: 현물가 < LTH 실현가  |  모멘텀 조건: STH-SOPR < 1.0 이후 1.0 회복")

# 2. 사이드바: 토큰, 기간 선택, 수동 최신화 입력
default_token = st.secrets.get("BGEOMETRICS_TOKEN", "") if hasattr(st, "secrets") else ""

with st.sidebar:
    st.header("⚙️ 설정 및 기간")
    api_token = st.text_input("BGeometrics API 토큰", value=default_token, type="password")

    period_option = st.radio(
        "조회 기간 선택",
        ["전체 (2015~현재)", "최근 4년", "최근 1년", "최근 6개월"],
        index=0
    )

    st.divider()
    st.header("✍️ 수동 최신화 입력 (선택)")
    st.caption("API 지연 시 최신 수치를 입력하면 오늘 기준으로 즉시 반영됩니다.")
    use_manual = st.checkbox("수동 최신값 적용하기", value=False)
    manual_lth = st.number_input("최신 LTH 실현가 ($)", min_value=0.0, value=0.0, step=100.0)
    manual_sopr = st.number_input("최신 STH-SOPR", min_value=0.0, max_value=5.0, value=0.0, step=0.005, format="%.3f")

    st.divider()
    st.caption("API 호출 제한 보호를 위해 시계열 데이터는 6시간마다 캐싱됩니다.")

# 3. 실시간 BTC 현물가 조회
@st.cache_data(ttl=60)
def get_live_btc_price():
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5).json()
        return float(res["price"])
    except Exception:
        return None

# 4. 과거 히스토리 CSV 로드
@st.cache_data
def load_historical_csv():
    try:
        hdf = pd.read_csv("historical_onchain.csv")
        hdf["date"] = pd.to_datetime(hdf["date"])
        for col in ["btc_price", "lth_rp", "sth_sopr"]:
            hdf[col] = pd.to_numeric(hdf[col], errors="coerce")
        hdf["source"] = "CSV"
        return hdf.dropna(subset=["date"])
    except FileNotFoundError:
        return pd.DataFrame(columns=["date", "btc_price", "lth_rp", "sth_sopr", "source"])

# 5. BGeometrics API 시계열 조회 (6시간 캐싱)
@st.cache_data(ttl=21600)
def fetch_api_data(token):
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
        date_col = "d" if "d" in df.columns else df.columns[0]
        val_col = [c for c in df.columns if c not in ["d", "unixTs"]][0]
        df = df[[date_col, val_col]].rename(columns={date_col: "date", val_col: key})
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df[key] = pd.to_numeric(df[key], errors="coerce")
        dfs[key] = df.dropna(subset=["date"])

    merged = dfs["btc_price"].merge(dfs["lth_rp"], on="date", how="outer")
    merged = merged.merge(dfs["sth_sopr"], on="date", how="outer")
    merged = merged.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    merged["source"] = "API"
    return merged

if not api_token:
    st.warning("왼쪽 사이드바(모바일은 좌상단 `>` 버튼)에 BGeometrics API 토큰을 입력해 주세요.")
    st.stop()

try:
    api_df = fetch_api_data(api_token)
    live_btc = get_live_btc_price()
except Exception as e:
    st.error(f"데이터 조회 중 오류가 발생했습니다.\n상세: {e}")
    st.stop()

hist_df = load_historical_csv()

# 6. 과거 CSV + API 병합 (API 날짜 우선)
if not hist_df.empty:
    hist_df["date"] = pd.to_datetime(hist_df["date"]).dt.normalize()
    api_min_date = api_df["date"].min()
    hist_older = hist_df[hist_df["date"] < api_min_date]
    combined = pd.concat([hist_older, api_df], ignore_index=True)
else:
    combined = api_df.copy()

combined = combined.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
combined["btc_price"] = combined["btc_price"].ffill()
combined["lth_rp_plot"] = combined["lth_rp"].ffill()

# 7. 수동 최신화 입력 반영 (오늘 날짜 행 추가/갱신)
today_utc = pd.Timestamp(datetime.now(timezone.utc).date())
manual_points = []

if use_manual:
    latest_known_btc = live_btc if live_btc else combined["btc_price"].dropna().iloc[-1]
    latest_known_lth = manual_lth if manual_lth > 0 else combined["lth_rp_plot"].dropna().iloc[-1]
    latest_known_sopr = manual_sopr if manual_sopr > 0 else np.nan

    new_row = {
        "date": today_utc,
        "btc_price": latest_known_btc,
        "lth_rp": manual_lth if manual_lth > 0 else np.nan,
        "lth_rp_plot": latest_known_lth,
        "sth_sopr": manual_sopr if manual_sopr > 0 else np.nan,
        "source": "MANUAL"
    }
    combined = combined[combined["date"] != today_utc]
    combined = pd.concat([combined, pd.DataFrame([new_row])], ignore_index=True).sort_values("date").reset_index(drop=True)
    combined["btc_price"] = combined["btc_price"].ffill()
    combined["lth_rp_plot"] = combined["lth_rp_plot"].ffill()

# 8. 각 지표의 실제 마지막 기준일 및 지연일 계산
lth_valid = combined.dropna(subset=["lth_rp"])
sopr_valid = combined.dropna(subset=["sth_sopr"])

last_lth_date = lth_valid["date"].iloc[-1] if not lth_valid.empty else today_utc
latest_lth_val = lth_valid["lth_rp"].iloc[-1] if not lth_valid.empty else combined["lth_rp_plot"].iloc[-1]
lth_lag_days = max(0, (today_utc - last_lth_date).days)

last_sopr_date = sopr_valid["date"].iloc[-1] if not sopr_valid.empty else today_utc
latest_sopr_val = sopr_valid["sth_sopr"].iloc[-1] if not sopr_valid.empty else np.nan
prev_sopr_val = sopr_valid["sth_sopr"].iloc[-2] if len(sopr_valid) > 1 else latest_sopr_val
sopr_lag_days = max(0, (today_utc - last_sopr_date).days)

current_btc = live_btc if live_btc else combined["btc_price"].dropna().iloc[-1]
gap_pct = ((current_btc - latest_lth_val) / latest_lth_val) * 100

# 9. 통합 신호등 판정 로직 (0: 일반/관망, 1: 저평가 진입/주의, 2: 바닥 변곡 포착)
def compute_signal_level(price, lth, sopr, prev_sopr):
    if pd.isna(price) or pd.isna(lth):
        return 0
    is_undervalued = price < lth
    is_recovering = (not pd.isna(sopr)) and (sopr >= 1.0) and ((pd.isna(prev_sopr)) or (prev_sopr < 1.0))
    if is_undervalued and is_recovering:
        return 2  # 초록 (강력 바닥 변곡점)
    elif is_undervalued:
        return 1  # 노랑 (저평가 공간 진입)
    else:
        return 0  # 회색/일반

# 시계열 전체 신호등 값 계산
combined["sopr_filled_for_signal"] = combined["sth_sopr"].ffill()
combined["prev_sopr_for_signal"] = combined["sopr_filled_for_signal"].shift(1)
combined["signal_level"] = [
    compute_signal_level(p, l, s, ps)
    for p, l, s, ps in zip(
        combined["btc_price"],
        combined["lth_rp_plot"],
        combined["sopr_filled_for_signal"],
        combined["prev_sopr_for_signal"]
    )
]

current_signal = compute_signal_level(current_btc, latest_lth_val, latest_sopr_val, prev_sopr_val)

# 10. 상단 KPI 및 시차 경고 배너
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("현재 BTC 가격", f"${current_btc:,.0f}")
with c2:
    st.metric(
        "LTH 실현 가격",
        f"${latest_lth_val:,.0f}",
        f"{gap_pct:+.1f}% (기준: {last_lth_date.strftime('%m/%d')}, D-{lth_lag_days})",
        delta_color="inverse" if current_btc < latest_lth_val else "normal"
    )
with c3:
    if pd.isna(latest_sopr_val):
        st.metric("STH-SOPR", "데이터 없음")
    else:
        sopr_state = "1.0 회복!" if (prev_sopr_val < 1.0 and latest_sopr_val >= 1.0) else ("손실(<1.0)" if latest_sopr_val < 1.0 else "이익(≥1.0)")
        st.metric(
            "STH-SOPR",
            f"{latest_sopr_val:.3f}",
            f"{sopr_state} ({last_sopr_date.strftime('%m/%d')}, D-{sopr_lag_days})"
        )
with c4:
    sig_label = {2: "🟢 초록 (변곡 포착)", 1: "🟡 노랑 (저평가 진입)", 0: "⚪ 대기/일반"}[current_signal]
    st.metric("통합 신호등", sig_label)

if sopr_lag_days >= 3 and not (use_manual and manual_sopr > 0):
    st.warning(
        f"⚠️ **지표 업데이트 시차 안내:** LTH 실현가는 `{last_lth_date.strftime('%Y-%m-%d')}` 기준이나, "
        f"STH-SOPR은 `{last_sopr_date.strftime('%Y-%m-%d')}`({sopr_lag_days}일 전) 데이터까지 반영되어 있습니다. "
        f"최신 값이 있다면 왼쪽 사이드바 **수동 최신화 입력**을 활용하세요."
    )

# 11. 기간 필터 적용
if period_option == "최근 6개월":
    cutoff = today_utc - pd.DateOffset(months=6)
elif period_option == "최근 1년":
    cutoff = today_utc - pd.DateOffset(years=1)
elif period_option == "최근 4년":
    cutoff = today_utc - pd.DateOffset(years=4)
else:
    cutoff = combined["date"].min()

view_df = combined[combined["date"] >= cutoff].copy().reset_index(drop=True)

# 12. 3단 통합 시각화 (상단: 가격 vs LTH 실현가 + 저평가 음영 / 중단: STH-SOPR + 1.0 영역 구분 / 하단: 통합 신호등)
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=True,
    vertical_spacing=0.06, row_heights=[0.50, 0.30, 0.20],
    subplot_titles=(
        "1. BTC 시장가 vs LTH 실현가 (녹색 음영 = 현물가 < LTH 실현가)",
        "2. STH-SOPR (1.0 이상 초록 영역 / 1.0 미만 빨강 영역)",
        "3. 통합 신호등 타임라인 (2=바닥변곡 / 1=저평가진입 / 0=대기)"
    )
)

# --- 1단: BTC 가격 & LTH 실현가 ---
fig.add_trace(go.Scatter(
    x=view_df["date"], y=view_df["btc_price"],
    name="BTC 시장가", line=dict(color="#F7931A", width=2)
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=view_df["date"], y=view_df["lth_rp_plot"],
    name="LTH 실현가", line=dict(color="#00D2FF", width=2)
), row=1, col=1)

# 저평가 구간(현물가 < LTH 실현가) 세그먼트별 음영 표시
under_mask = view_df["btc_price"] < view_df["lth_rp_plot"]
seg_id = (under_mask != under_mask.shift()).cumsum()
for _, grp in view_df[under_mask].groupby(seg_id):
    if len(grp) >= 1:
        x_poly = list(grp["date"]) + list(grp["date"])[::-1]
        y_poly = list(grp["btc_price"]) + list(grp["lth_rp_plot"])[::-1]
        fig.add_trace(go.Scatter(
            x=x_poly, y=y_poly,
            fill="toself", fillcolor="rgba(0, 255, 136, 0.25)",
            line=dict(color="rgba(0,0,0,0)"),
            showlegend=False, hoverinfo="skip"
        ), row=1, col=1)

# --- 2단: STH-SOPR & 1.0 기준 상/하단 영역 구분 ---
sopr_min_y = min(0.6, float(view_df["sth_sopr"].min()) * 0.95) if view_df["sth_sopr"].notna().any() else 0.6
sopr_max_y = max(1.4, float(view_df["sth_sopr"].max()) * 1.05) if view_df["sth_sopr"].notna().any() else 1.4

fig.add_hrect(
    y0=1.0, y1=sopr_max_y,
    fillcolor="rgba(34, 197, 94, 0.10)", line_width=0, row=2, col=1
)
fig.add_hrect(
    y0=sopr_min_y, y1=1.0,
    fillcolor="rgba(239, 68, 68, 0.10)", line_width=0, row=2, col=1
)
fig.add_hline(y=1.0, line_dash="dash", line_color="#EF4444", row=2, col=1)

fig.add_trace(go.Scatter(
    x=view_df["date"], y=view_df["sth_sopr"],
    name="STH-SOPR", line=dict(color="#C084FC", width=1.8),
    connectgaps=False
), row=2, col=1)

# --- 3단: 통합 신호등 타임라인 ---
color_map = {2: "#22C55E", 1: "#EAB308", 0: "#6B7280"}
bar_colors = [color_map.get(int(v), "#6B7280") for v in view_df["signal_level"]]

fig.add_trace(go.Bar(
    x=view_df["date"], y=view_df["signal_level"],
    name="통합 신호등", marker_color=bar_colors
), row=3, col=1)

fig.update_layout(
    height=780,
    template="plotly_dark",
    hovermode="x unified",
    margin=dict(l=10, r=10, t=45, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)
fig.update_yaxes(type="log", row=1, col=1)
fig.update_yaxes(range=[sopr_min_y, sopr_max_y], row=2, col=1)
fig.update_yaxes(tickvals=[0, 1, 2], ticktext=["0:대기", "1:저평가", "2:변곡"], range=[0, 2.3], row=3, col=1)

st.plotly_chart(fig, use_container_width=True)

# 13. 하단 최근 데이터 요약 테이블
st.subheader("📋 최근 데이터 요약 (최근 15건)")
tbl = view_df.tail(15).sort_values("date", ascending=False).copy()
tbl["날짜"] = tbl["date"].dt.strftime("%Y-%m-%d")
tbl["출처"] = tbl["source"]
tbl["BTC ($)"] = tbl["btc_price"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "-")
tbl["LTH 실현가 ($)"] = tbl["lth_rp_plot"].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "-")
tbl["괴리율"] = np.where(
    tbl["btc_price"].notna() & tbl["lth_rp_plot"].notna(),
    ((tbl["btc_price"] - tbl["lth_rp_plot"]) / tbl["lth_rp_plot"] * 100),
    np.nan
)
tbl["괴리율"] = [f"{x:+.1f}%" if pd.notna(x) else "-" for x in tbl["괴리율"]]
tbl["STH-SOPR"] = tbl["sth_sopr"].map(lambda x: f"{x:.3f}" if pd.notna(x) else "지연/없음")
tbl["신호등"] = tbl["signal_level"].map({2: "🟢 변곡", 1: "🟡 저평가", 0: "⚪ 대기"})

st.dataframe(
    tbl[["날짜", "출처", "BTC ($)", "LTH 실현가 ($)", "괴리율", "STH-SOPR", "신호등"]],
    use_container_width=True,
    hide_index=True
)
