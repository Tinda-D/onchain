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

# 반감기 기준일 정의 (과거 확정일 + 2028년 4월 예상일)
HALVINGS = [
    ("2016-07-09", "반감기 2016", False),
    ("2020-05-11", "반감기 2020", False),
    ("2024-04-20", "반감기 2024", False),
    ("2028-04-17", "다음 반감기(2028 예상)", True),
]
NEXT_HALVING_DATE = pd.Timestamp("2028-04-17")

# 2. 사이드바: 토큰, 기간 선택, 바로가기 링크 및 수동 최신화 입력
default_token = st.secrets.get("BGEOMETRICS_TOKEN", "") if hasattr(st, "secrets") else ""

with st.sidebar:
    st.header("⚙️ 설정 및 기간")
    api_token = st.text_input("BGeometrics API 토큰", value=default_token, type="password")

    period_option = st.radio(
        "조회 기간 선택",
        ["전체 (2015~현재)", "최근 4년", "최근 1년", "최근 6개월"],
        index=0
    )

    expand_next_halving = st.toggle(
        "다음 반감기(2028 예상)까지 차트 확장",
        value=False,
        help="켜면 차트 오른쪽 끝을 2028년 4월 예상 반감기 시점까지 넓혀 표시합니다."
    )

    st.divider()
    st.header("🔗 지표 확인 및 수동 입력")
    st.caption("페이지에서 최신 수치를 확인한 뒤 아래에 입력하세요.")

    st.link_button("📈 Bitbo 온체인 차트 열기", "https://charts.bitbo.io/", use_container_width=True)
    st.link_button("💰 LTH Realized Price 차트", "https://charts.bitbo.io/lth-realized-price/", use_container_width=True)
    st.link_button("📉 STH-SOPR 차트", "https://charts.bitbo.io/sth-sopr/", use_container_width=True)

    use_manual = st.checkbox("수동 최신값 적용하기", value=False)
    manual_lth = st.number_input("최신 LTH 실현가 ($)", min_value=0.0, value=0.0, step=100.0)
    manual_sopr = st.number_input("최신 STH-SOPR", min_value=0.0, max_value=5.0, value=0.0, step=0.005, format="%.3f")

    st.divider()
    st.caption("시계열 데이터는 무료 API 호출 제한 보호를 위해 6시간마다 캐싱됩니다.")

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

if use_manual:
    latest_known_btc = live_btc if live_btc else combined["btc_price"].dropna().iloc[-1]
    latest_known_lth = manual_lth if manual_lth > 0 else combined["lth_rp_plot"].dropna().iloc[-1]

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

# 8. 각 지표의 실제 마지막 기준일 및 차트 반영 끝일 계산
lth_valid = combined.dropna(subset=["lth_rp"])
lth_plot_valid = combined.dropna(subset=["lth_rp_plot"])
sopr_valid = combined.dropna(subset=["sth_sopr"])

last_lth_date = lth_valid["date"].iloc[-1] if not lth_valid.empty else today_utc
last_lth_plot_date = lth_plot_valid["date"].iloc[-1] if not lth_plot_valid.empty else last_lth_date
latest_lth_val = lth_valid["lth_rp"].iloc[-1] if not lth_valid.empty else combined["lth_rp_plot"].iloc[-1]
lth_lag_days = max(0, (today_utc - last_lth_date).days)

last_sopr_date = sopr_valid["date"].iloc[-1] if not sopr_valid.empty else today_utc
latest_sopr_val = sopr_valid["sth_sopr"].iloc[-1] if not sopr_valid.empty else np.nan
prev_sopr_val = sopr_valid["sth_sopr"].iloc[-2] if len(sopr_valid) > 1 else np.nan
prev2_sopr_val = sopr_valid["sth_sopr"].iloc[-3] if len(sopr_valid) > 2 else np.nan
sopr_lag_days = max(0, (today_utc - last_sopr_date).days)

current_btc = live_btc if live_btc else combined["btc_price"].dropna().iloc[-1]
btc_is_live = live_btc is not None
gap_pct = ((current_btc - latest_lth_val) / latest_lth_val) * 100

# 9. 통합 신호등 판정 로직
def compute_signal_level(price, lth, sopr, prev_sopr, prev2_sopr):
    if pd.isna(price) or pd.isna(lth):
        return 0
    is_undervalued = price < lth
    two_day_recovery = (
        (not pd.isna(sopr)) and (sopr >= 1.0) and
        (not pd.isna(prev_sopr)) and (prev_sopr >= 1.0) and
        ((pd.isna(prev2_sopr)) or (prev2_sopr < 1.0))
    )
    if is_undervalued and two_day_recovery:
        return 2
    elif is_undervalued:
        return 1
    else:
        return 0

combined["sopr_for_signal"] = combined["sth_sopr"].ffill()
combined["prev1_sopr"] = combined["sopr_for_signal"].shift(1)
combined["prev2_sopr"] = combined["sopr_for_signal"].shift(2)
combined["signal_level"] = [
    compute_signal_level(p, l, s, p1, p2)
    for p, l, s, p1, p2 in zip(
        combined["btc_price"],
        combined["lth_rp_plot"],
        combined["sopr_for_signal"],
        combined["prev1_sopr"],
        combined["prev2_sopr"]
    )
]
combined["signal_text"] = combined["signal_level"].map({
    2: "🟢 변곡 (현물가 < LTH & STH-SOPR 2일 연속 ≥1.0 회복)",
    1: "🟡 저평가 (현물가 < LTH 실현가 / 2일 연속 회복 대기)",
    0: "⚪ 대기 (현물가 ≥ LTH 실현가)"
})
combined["signal_short"] = combined["signal_level"].map({
    2: "🟢 변곡",
    1: "🟡 저평가",
    0: "⚪ 대기"
})

current_signal = compute_signal_level(current_btc, latest_lth_val, latest_sopr_val, prev_sopr_val, prev2_sopr_val)
sig_title = {2: "🟢 변곡 포착", 1: "🟡 저평가 진입", 0: "⚪ 대기 구간"}[current_signal]

# 10. 상단 헤더 + 데이터 기준일 안내 + KPI
with st.container(border=True):
    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.markdown("### ₿ 비트코인 온체인 바닥 대시보드")
        st.caption("공간 조건: 현물가 < LTH 실현가(저평가)  |  모멘텀 조건: STH-SOPR < 1.0 이후 2일 연속 1.0 이상 회복(변곡)")
    with top_r:
        st.metric("현재 신호", sig_title)

    lth_date_str = (
        f"{last_lth_date.strftime('%Y-%m-%d')} (D-{lth_lag_days}, 선 ~{last_lth_plot_date.strftime('%m/%d')})"
        if last_lth_plot_date != last_lth_date
        else f"{last_lth_date.strftime('%Y-%m-%d')} (D-{lth_lag_days})"
    )
    btc_date_str = "실시간 시세(1분 갱신)" if btc_is_live else f"{today_utc.strftime('%Y-%m-%d')} 기준"
    st.caption(
        f"⏱️ **현물가:** {btc_date_str}  ·  "
        f"📌 **LTH 실현가 원본 기준일:** {lth_date_str}  ·  "
        f"📌 **STH-SOPR 기준일:** {last_sopr_date.strftime('%Y-%m-%d')} (D-{sopr_lag_days})"
    )

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric(
        "현재 BTC 가격",
        f"${current_btc:,.0f}",
        "실시간(Live)" if btc_is_live else "최근 종가"
    )
with c2:
    st.metric(
        "LTH 실현 가격",
        f"${latest_lth_val:,.0f}",
        f"괴리 {gap_pct:+.1f}% · 원본 {last_lth_date.strftime('%m/%d')}",
        delta_color="inverse" if current_btc < latest_lth_val else "normal"
    )
with c3:
    if pd.isna(latest_sopr_val):
        st.metric("STH-SOPR", "데이터 없음", "기준일 미확인")
    else:
        if (not pd.isna(prev_sopr_val)) and (latest_sopr_val >= 1.0 and prev_sopr_val >= 1.0):
            sopr_state = "2일 연속 ≥1.0"
        elif latest_sopr_val >= 1.0:
            sopr_state = "1.0 첫 돌파"
        else:
            sopr_state = "손실(<1.0)"
        st.metric(
            "STH-SOPR",
            f"{latest_sopr_val:.3f}",
            f"{sopr_state} · {last_sopr_date.strftime('%Y-%m-%d')}"
        )
with c4:
    st.metric("통합 신호등", sig_title, f"LTH D-{lth_lag_days} / SOPR D-{sopr_lag_days}")

if current_signal == 2:
    st.success("🟢 **변곡(2일 연속 회복 확인):** 현물가가 LTH 실현가 아래에 있고, STH-SOPR이 1.0 미만 구간을 지나 **2일 연속 1.0 이상**을 유지한 시점입니다.")
elif current_signal == 1:
    st.info("🟡 **저평가(공간 진입·회복 대기):** 현물가가 LTH 실현가 아래에 있습니다. STH-SOPR이 1.0을 넘어 **2일 연속 안착**하는지 확인하는 단계입니다.")
else:
    st.caption("⚪ **대기(일반 구간):** 현재가가 장기보유자 평균 매수가(LTH 실현가) 위에 있어 저평가 공간 조건에 해당하지 않는 상태입니다.")

with st.expander("📖 통합 신호등 용어 및 판독 기준 보기 (대기 / 저평가 / 변곡)"):
    st.markdown("""
    - **⚪ 대기 (0단계):** **현물가 ≥ LTH 실현가**인 일반 구간입니다. 장기보유자 평균 매수가보다 비싸게 거래되는 상태입니다.
    - **🟡 저평가 (1단계):** **현물가 < LTH 실현가**인 저평가 진입 구간입니다. 단기보유자 손익비율(STH-SOPR)이 `1.0` 위로 올라와 **2일 연속 유지**되는 모멘텀 전환을 기다리는 구간입니다.
    - **🟢 변곡 (2단계):** **현물가 < LTH 실현가** 상태에서 **STH-SOPR이 `1.0` 미만 구간을 거친 뒤 2일 연속 `1.0` 이상**을 기록한 구간입니다. 하루짜리 반등 착시를 줄이기 위해 2일 연속 안착 기준으로 판정합니다.
    """)

if (lth_lag_days >= 2 or sopr_lag_days >= 2) and not use_manual:
    st.warning(
        f"⚠️ **온체인 데이터 기준일 안내:** 온체인 지표는 일별 집계 특성상 실시간 시세와 시차가 있습니다. "
        f"(LTH 실현가 원본: `{last_lth_date.strftime('%Y-%m-%d')}` / STH-SOPR: `{last_sopr_date.strftime('%Y-%m-%d')}`) "
        f"사이드바 링크로 최신 값을 확인한 뒤 **수동 최신값 적용하기**를 사용할 수 있습니다."
    )

# 11. 기간 필터 및 다음 반감기 확장 범위 설정
if period_option == "최근 6개월":
    cutoff = today_utc - pd.DateOffset(months=6)
elif period_option == "최근 1년":
    cutoff = today_utc - pd.DateOffset(years=1)
elif period_option == "최근 4년":
    cutoff = today_utc - pd.DateOffset(years=4)
else:
    cutoff = combined["date"].min()

view_df = combined[combined["date"] >= cutoff].copy().reset_index(drop=True)
min_view_date = view_df["date"].min() if not view_df.empty else cutoff
data_max_date = view_df["date"].max() if not view_df.empty else today_utc
display_end_date = max(data_max_date, NEXT_HALVING_DATE) if expand_next_halving else data_max_date

x_pad_left = max(pd.Timedelta(days=2), (display_end_date - min_view_date) * 0.015)
x_pad_right = max(pd.Timedelta(days=15), (display_end_date - min_view_date) * 0.025)
x_range = [min_view_date - x_pad_left, display_end_date + x_pad_right]

# 12. 3단 통합 시각화
fig = make_subplots(
    rows=3, cols=1, shared_xaxes=False,
    vertical_spacing=0.06, row_heights=[0.52, 0.30, 0.18],
    subplot_titles=("BTC / LTH 실현가", "STH-SOPR", "통합 신호등 (대기 · 저평가 · 변곡)")
)

# --- 1단: BTC 가격 & LTH 실현가 ---
fig.add_trace(go.Scatter(
    x=view_df["date"], y=view_df["btc_price"],
    name="BTC 시장가", line=dict(color="#F7931A", width=2),
    hovertemplate="BTC: $%{y:,.0f}<extra></extra>"
), row=1, col=1)

fig.add_trace(go.Scatter(
    x=view_df["date"], y=view_df["lth_rp_plot"],
    name="LTH 실현가", line=dict(color="#00D2FF", width=2),
    hovertemplate="LTH 실현가: $%{y:,.0f}<extra></extra>"
), row=1, col=1)

# 저평가 구간(현물가 < LTH 실현가) 음영 표시
under_mask = view_df["btc_price"] < view_df["lth_rp_plot"]
seg_id = (under_mask != under_mask.shift()).cumsum()
first_under = True
for _, grp in view_df[under_mask].groupby(seg_id):
    if len(grp) >= 1:
        x_poly = list(grp["date"]) + list(grp["date"])[::-1]
        y_poly = list(grp["btc_price"]) + list(grp["lth_rp_plot"])[::-1]
        fig.add_trace(go.Scatter(
            x=x_poly, y=y_poly,
            fill="toself", fillcolor="rgba(34, 197, 94, 0.24)",
            line=dict(color="rgba(0,0,0,0)"),
            name="저평가(<LTH)", showlegend=first_under, hoverinfo="skip"
        ), row=1, col=1)
        first_under = False

# --- 2단: STH-SOPR ---
sopr_min_y = min(0.65, float(view_df["sth_sopr"].min()) * 0.95) if view_df["sth_sopr"].notna().any() else 0.65
sopr_max_y = max(1.35, float(view_df["sth_sopr"].max()) * 1.05) if view_df["sth_sopr"].notna().any() else 1.35

fig.add_trace(go.Scatter(
    x=view_df["date"], y=np.ones(len(view_df)),
    line=dict(color="rgba(0,0,0,0)"),
    hoverinfo="skip", showlegend=False
), row=2, col=1)

fig.add_trace(go.Scatter(
    x=view_df["date"], y=view_df["sth_sopr"],
    name="STH-SOPR",
    line=dict(color="#C084FC", width=1.9),
    fill="tonexty",
    fillcolor="rgba(239, 68, 68, 0.10)",
    connectgaps=False,
    hovertemplate="STH-SOPR: %{y:.3f}<extra></extra>"
), row=2, col=1)

fig.add_hline(y=1.0, line_dash="dash", line_color="#9CA3AF", line_width=1.2, row=2, col=1)

# --- 3단: 통합 신호등 ---
color_map = {2: "#22C55E", 1: "#EAB308", 0: "#4B5563"}
bar_colors = [color_map.get(int(v), "#4B5563") for v in view_df["signal_level"]]

fig.add_trace(go.Bar(
    x=view_df["date"],
    y=np.ones(len(view_df)),
    name="신호등",
    marker=dict(color=bar_colors, line=dict(width=0)),
    customdata=view_df["signal_text"],
    hovertemplate="%{customdata}<extra></extra>"
), row=3, col=1)

# 모든 트레이스를 단일 X축(x3)으로 바인딩하여 3단 관통 수직 점선 구현
fig.update_traces(xaxis="x3")

# 반감기 세로선 및 라벨 (x3 기준)
for h_date_str, h_label, is_future in HALVINGS:
    if is_future and not expand_next_halving:
        continue
    h_ts = pd.Timestamp(h_date_str)
    if min_view_date <= h_ts <= display_end_date:
        line_color = "#38BDF8" if is_future else "#FACC15"
        fig.add_shape(
            type="line",
            x0=h_ts, x1=h_ts,
            y0=0, y1=1,
            xref="x3", yref="y domain",
            line=dict(color=line_color, width=1.4, dash="dot")
        )
        fig.add_annotation(
            x=h_ts, y=1.0,
            xref="x3", yref="y domain",
            text=h_label, showarrow=False,
            yshift=10, font=dict(size=10, color=line_color)
        )

fig.update_layout(
    height=770,
    template="plotly_dark",
    hovermode="x unified",
    margin=dict(l=10, r=10, t=45, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    xaxis=dict(visible=False),
    xaxis2=dict(visible=False),
    xaxis3=dict(
        range=x_range,
        autorange=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="#F8FAFC",
        spikethickness=1.2,
        spikedash="dot"
    )
)
fig.update_yaxes(
    type="log",
    showspikes=True,
    spikemode="toaxis",
    spikesnap="cursor",
    spikecolor="#F59E0B",
    spikethickness=1.2,
    spikedash="dash",
    row=1, col=1
)
fig.update_yaxes(range=[sopr_min_y, sopr_max_y], row=2, col=1)
fig.update_yaxes(showticklabels=False, range=[0, 1.2], fixedrange=True, row=3, col=1)

st.plotly_chart(fig, use_container_width=True, key=f"btc_onchain_chart_{period_option}_{expand_next_halving}")

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
tbl["신호등"] = tbl["signal_short"]

st.dataframe(
    tbl[["날짜", "출처", "BTC ($)", "LTH 실현가 ($)", "괴리율", "STH-SOPR", "신호등"]],
    use_container_width=True,
    hide_index=True
)
