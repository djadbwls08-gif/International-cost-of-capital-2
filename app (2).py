"""
International Cost of Capital App
---------------------------------
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, with dated fallbacks if a source is unreachable):
  * Unlevered industry betas  - Aswath Damodaran, betas.xls (U.S.)
  * BBB (Baa2) USD spread     - Aswath Damodaran, ratings.html (large non-financial firms)
  * Corporate tax rates       - OECD Corporate Tax Statistics (combined statutory CIT rate)
  * 10Y government yields     - Trading Economics, /bonds
"""

import io
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Page config
# ----------------------------------------------------------------------------
st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 25

URL_BETAS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
URL_RATINGS = [
    "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html",
]
URL_OECD_PAGE = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
URL_OECD_API = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
URL_TE = "https://tradingeconomics.com/bonds"

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"

# ----------------------------------------------------------------------------
# Fallback data (used ONLY if a live fetch fails; clearly labelled in the UI)
# ----------------------------------------------------------------------------
FALLBACK_BETAS = {  # Damodaran, U.S. "Unlevered beta" column, data as of January 2026
    "Advertising": 0.93, "Aerospace/Defense": 0.85, "Air Transport": 0.70, "Apparel": 0.76,
    "Auto & Truck": 1.27, "Auto Parts": 1.02, "Bank (Money Center)": 0.34, "Banks (Regional)": 0.29,
    "Beverage (Alcoholic)": 0.61, "Beverage (Soft)": 0.56, "Broadcasting": 0.29,
    "Brokerage & Investment Banking": 0.58, "Building Materials": 0.93,
    "Business & Consumer Services": 0.77, "Cable TV": 0.35, "Chemical (Basic)": 0.58,
    "Chemical (Diversified)": 0.37, "Chemical (Specialty)": 0.79, "Coal & Related Energy": 1.02,
    "Computer Services": 0.92, "Computers/Peripherals": 1.31, "Construction Supplies": 1.02,
    "Diversified": 0.79, "Drugs (Biotechnology)": 1.03, "Drugs (Pharmaceutical)": 0.89,
    "Education": 0.66, "Electrical Equipment": 1.15, "Electronics (Consumer & Office)": 0.83,
    "Electronics (General)": 0.90, "Engineering/Construction": 1.09, "Entertainment": 0.74,
    "Environmental & Waste Services": 0.81, "Farming/Agriculture": 0.81,
    "Financial Svcs. (Non-bank & Insurance)": 0.32, "Food Processing": 0.46,
    "Food Wholesalers": 0.64, "Furn/Home Furnishings": 0.62, "Green & Renewable Energy": 0.46,
    "Healthcare Products": 0.83, "Healthcare Support Services": 0.69,
    "Heathcare Information and Technology": 0.99, "Homebuilding": 0.78,
    "Hospitals/Healthcare Facilities": 0.55, "Hotel/Gaming": 0.83, "Household Products": 0.72,
    "Information Services": 0.74, "Insurance (General)": 0.56, "Insurance (Life)": 0.43,
    "Insurance (Prop/Cas.)": 0.44, "Investments & Asset Management": 0.53, "Machinery": 0.87,
    "Metals & Mining": 0.96, "Office Equipment & Services": 0.98, "Oil/Gas (Integrated)": 0.27,
    "Oil/Gas (Production and Exploration)": 0.56, "Oil/Gas Distribution": 0.47,
    "Oilfield Svcs/Equip.": 0.74, "Packaging & Container": 0.72, "Paper/Forest Products": 0.72,
    "Power": 0.31, "Precious Metals": 0.79, "Publishing & Newspapers": 0.48, "R.E.I.T.": 0.39,
    "Real Estate (Development)": 0.48, "Real Estate (General/Diversified)": 0.58,
    "Real Estate (Operations & Services)": 0.81, "Recreation": 0.70, "Reinsurance": 0.44,
    "Restaurant/Dining": 0.77, "Retail (Automotive)": 0.70, "Retail (Building Supply)": 1.31,
    "Retail (Distributors)": 0.78, "Retail (General)": 0.76, "Retail (Grocery and Food)": 0.80,
    "Retail (REITs)": 0.44, "Retail (Special Lines)": 0.95, "Rubber& Tires": 0.14,
    "Semiconductor": 1.49, "Semiconductor Equip": 1.35, "Shipbuilding & Marine": 0.64,
    "Shoe": 0.93, "Software (Entertainment)": 1.01, "Software (Internet)": 1.55,
    "Software (System & Application)": 1.23, "Steel": 0.90, "Telecom (Wireless)": 0.39,
    "Telecom. Equipment": 0.86, "Telecom. Services": 0.37, "Tobacco": 0.68,
    "Transportation": 0.68, "Transportation (Railroads)": 0.81, "Trucking": 0.85,
    "Utility (General)": 0.15, "Utility (Water)": 0.28, "Total Market": 0.72,
    "Total Market (without financials)": 0.88,
}
FALLBACK_BETAS_DATE = "January 2026 (static fallback)"

FALLBACK_BBB = 0.0111  # Damodaran ratings.html, Baa2/BBB large non-financial, Jan 2026
FALLBACK_BBB_DATE = "January 2026 (static fallback)"

FALLBACK_TAX = {  # OECD combined (central + sub-central) statutory CIT rates, approx. latest year
    "United States": 25.57, "South Korea": 26.40, "Japan": 29.74, "Germany": 29.94,
    "United Kingdom": 25.00, "France": 36.13, "Italy": 27.81, "Canada": 26.14,
    "Australia": 30.00, "Mexico": 30.00, "Brazil": 34.00, "India": 25.17, "China": 25.00,
    "Spain": 25.00, "Netherlands": 25.80, "Switzerland": 19.61, "Ireland": 12.50,
    "Singapore": 17.00, "Hong Kong": 16.50, "Indonesia": 22.00, "South Africa": 27.00,
    "Chile": 27.00, "Turkey": 25.00, "Sweden": 20.60, "Norway": 22.00, "Poland": 19.00,
    "Israel": 23.00, "New Zealand": 28.00, "Saudi Arabia": 20.00, "Vietnam": 20.00,
    "Thailand": 20.00, "Philippines": 25.00, "Argentina": 35.00, "Colombia": 35.00,
    "Belgium": 25.00, "Austria": 23.00, "Denmark": 22.00, "Finland": 20.00, "Portugal": 31.50,
    "Greece": 22.00, "Czech Republic": 21.00, "Hungary": 9.00, "Malaysia": 24.00,
    "Peru": 29.50, "Taiwan": 20.00, "Egypt": 22.50, "Nigeria": 30.00, "Kenya": 30.00,
    "Pakistan": 29.00, "Slovakia": 21.00,
}
FALLBACK_TAX_DATE = "OECD Corporate Tax Statistics, latest year (static fallback)"


# OECD naming -> Trading Economics naming
NAME_ALIASES = {
    "Korea": "South Korea", "Korea, Republic of": "South Korea",
    "China (People's Republic of)": "China", "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Czechia": "Czech Republic", "Hong Kong, China": "Hong Kong", "Hong Kong (China)": "Hong Kong",
    "Viet Nam": "Vietnam", "Slovak Republic": "Slovakia", "Russian Federation": "Russia",
    "Chinese Taipei": "Taiwan", "United States of America": "United States",
}


def norm_name(name: str) -> str:
    name = str(name).strip()
    return NAME_ALIASES.get(name, name)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(text) -> float:
    """'1.11%' -> 0.0111 ; '1.11' -> 0.0111"""
    m = re.search(r"-?\d+(\.\d+)?", str(text).replace(",", ""))
    return float(m.group()) / 100 if m else float("nan")


# ----------------------------------------------------------------------------
# Data fetchers (cached; the Refresh button clears the cache)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_betas():
    try:
        r = requests.get(URL_BETAS, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        raw = pd.read_excel(io.BytesIO(r.content), sheet_name="Industry Averages", header=None)
        hdr_row = raw.index[raw.apply(lambda s: s.astype(str).str.strip().eq("Industry Name").any(), axis=1)][0]
        df = raw.iloc[hdr_row + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr_row]]
        df = df[["Industry Name", "Unlevered beta"]].dropna()
        df["Unlevered beta"] = pd.to_numeric(df["Unlevered beta"], errors="coerce")
        df = df.dropna()
        df["Industry Name"] = df["Industry Name"].astype(str).str.strip()
        # Date: look for 'Date updated' in the workbook, else HTTP Last-Modified
        date = None
        text = " ".join(raw.astype(str).fillna("").values.ravel())
        m = re.search(r"Date updated:\s*([^\s].{0,20}?)(?:\s{2,}|nan|$)", text)
        if m:
            date = m.group(1).strip()
        if not date:
            date = r.headers.get("Last-Modified", "n/a")
        return df.reset_index(drop=True), f"{date} (live, fetched {now_utc()})", True
    except Exception as e:  # noqa: BLE001
        df = pd.DataFrame(list(FALLBACK_BETAS.items()), columns=["Industry Name", "Unlevered beta"])
        return df, f"{FALLBACK_BETAS_DATE} - live fetch failed: {type(e).__name__}", False


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def fetch_bbb_spread():
    last_err = None
    for url in URL_RATINGS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            page_text = soup.get_text(" ", strip=True)
            m = re.search(r"Data used is as of\s+([A-Za-z]+\s+\d{4})", page_text)
            date = m.group(1) if m else r.headers.get("Last-Modified", "n/a")
            # First Baa2/BBB row in document order = table for LARGE non-financial firms
            for tr in soup.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                for i, c in enumerate(cells):
                    if c.replace(" ", "") == "Baa2/BBB" and i + 1 < len(cells):
                        val = _pct(cells[i + 1])
                        if 0 < val < 0.2:
                            return val, f"{date} (live, fetched {now_utc()})", True
            raise ValueError("Baa2/BBB row not found")
        except Exception as e:  # noqa: BLE001
            last_err = e
    return FALLBACK_BBB, f"{FALLBACK_BBB_DATE} - live fetch failed: {type(last_err).__name__}", False


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_tax_rates():
    """OECD combined statutory CIT rate, latest year per jurisdiction (OECD SDMX API)."""
    try:
        r = requests.get(URL_OECD_API, headers=HEADERS, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        # find the label column that describes the rate type and keep 'Combined' standard rate
        rate_col = None
        for c in df.columns:
            if not pd.api.types.is_numeric_dtype(df[c]) and df[c].astype(str).str.contains("Combined", case=False).any():
                rate_col = c
                break
        if rate_col is None:
            raise ValueError("no 'Combined' rate column")
        mask = df[rate_col].astype(str).str.contains("Combined", case=False)
        for c in df.columns:  # exclude targeted / small-business rates if such a column exists
            if not pd.api.types.is_numeric_dtype(df[c]):
                bad = df[c].astype(str).str.contains("targeted|small", case=False, regex=True)
                if bad.any() and not bad.all():
                    mask &= ~bad
        sub = df[mask].copy()
        area_col = "Reference area" if "Reference area" in sub.columns else "REF_AREA"
        sub["TIME_PERIOD"] = pd.to_numeric(sub["TIME_PERIOD"], errors="coerce")
        sub = sub.dropna(subset=["OBS_VALUE"]).sort_values("TIME_PERIOD")
        latest = sub.groupby(area_col).tail(1)
        out = pd.DataFrame({
            "Country": latest[area_col].map(norm_name),
            "Tax rate": latest["OBS_VALUE"].astype(float) / 100,
            "Year": latest["TIME_PERIOD"].astype(int),
        }).drop_duplicates("Country").reset_index(drop=True)
        if len(out) < 20 or "United States" not in set(out["Country"]):
            raise ValueError("unexpected OECD response")
        yr = int(out["Year"].mode().iloc[0])
        return out, f"OECD Corporate Tax Statistics, tax year {yr} (live, fetched {now_utc()})", True
    except Exception as e:  # noqa: BLE001
        out = pd.DataFrame(
            [(k, v / 100, None) for k, v in FALLBACK_TAX.items()], columns=["Country", "Tax rate", "Year"]
        )
        return out, f"{FALLBACK_TAX_DATE} - live fetch failed: {type(e).__name__}", False


TE_COUNTRIES = [  # countries whose TE page /<country>/government-bond-yield has a 10Y quote (checked Nov 2026)
    "Australia", "Austria", "Belgium", "Brazil", "Canada", "Chile", "China", "Colombia", "Croatia",
    "Czech Republic", "Denmark", "Finland", "France", "Germany", "Greece", "Hong Kong", "Hungary", "Iceland",
    "India", "Indonesia", "Ireland", "Israel", "Italy", "Japan", "Kenya", "Lithuania", "Malaysia", "Mexico",
    "Netherlands", "New Zealand", "Nigeria", "Norway", "Pakistan", "Philippines", "Poland", "Portugal",
    "Romania", "Russia", "Singapore", "Slovakia", "Slovenia", "South Africa", "South Korea", "Spain",
    "Sweden", "Switzerland", "Taiwan", "Thailand", "Turkey", "United Kingdom", "United States", "Vietnam",
    "Zambia",
]


def te_url(country: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", country.lower()).strip("-")
    return f"https://tradingeconomics.com/{slug}/government-bond-yield"


@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_yield(country: str):
    """
    10Y yield for one country from Trading Economics.

    The summary page tradingeconomics.com/bonds loads its tables with JavaScript behind a bot check,
    so a plain request receives no table. Each country's bond page, however, is server-rendered:
    its first table ('Bonds | Yield | ... | Date') has a row '<Country> 10Y' with the latest quote.
    Returns (yield as decimal or NaN, quote date, status message).
    """
    url = te_url(country)
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200 or not r.text:
            raise ValueError(f"HTTP {r.status_code}")
        soup = BeautifulSoup(r.text, "html.parser")
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.select("thead th")]
            if "Yield" not in headers:
                continue
            iy = headers.index("Yield")
            idate = headers.index("Date") if "Date" in headers else None
            for tr in table.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if cells and cells[0].endswith("10Y") and len(cells) > iy:
                    y = float(cells[iy].replace(",", ""))
                    date = cells[idate] if idate is not None and len(cells) > idate else ""
                    return y / 100, date, "live"
        raise ValueError("10Y row not found")
    except Exception as e:  # noqa: BLE001
        return float("nan"), "", f"fetch failed: {type(e).__name__} {e}"[:120]


# ----------------------------------------------------------------------------
# Core calculation (pure function - easy to verify)
# ----------------------------------------------------------------------------
def compute(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_usd, spread_fc_override=None):
    """
    All inputs are decimals (e.g. 0.065 for 6.5%).

    1) Relever beta (Hamada):   bL = bU * [1 + (1 - t) * D/E],  D/E = d / (1 - d)
    2) USD cost of equity:      ke_$  = rf_US + bL * ERP
    3) USD cost of debt:        kd_$  = rf_US + BBB spread_USD
    4) Foreign-currency costs via relative (international) Fisher / interest-rate parity:
           ke_FC = (1 + ke_$) * (1 + rf_FC) / (1 + rf_US) - 1
           kd_FC = (1 + kd_$) * (1 + rf_FC) / (1 + rf_US) - 1        (auto)
           => FC spread s* = kd_FC - rf_FC = s * (1 + rf_FC) / (1 + rf_US)   (class slide 584)
       If the FC spread is overridden:  kd_FC = rf_FC + spread_FC_manual
    5) WACC = (1 - d) * ke + d * kd * (1 - t)   (same t, d, ERP in both currencies)
    """
    d = debt_share
    de = d / (1 - d) if d < 1 else float("inf")
    beta_l = beta_u * (1 + (1 - tax) * de)

    ke_usd = rf_us + beta_l * erp
    kd_usd = rf_us + spread_usd
    wacc_usd = (1 - d) * ke_usd + d * kd_usd * (1 - tax)

    fx = (1 + rf_fc) / (1 + rf_us)
    ke_fc = (1 + ke_usd) * fx - 1
    if spread_fc_override is None:
        kd_fc = (1 + kd_usd) * fx - 1
        spread_fc = kd_fc - rf_fc
    else:
        spread_fc = spread_fc_override
        kd_fc = rf_fc + spread_fc
    wacc_fc = (1 - d) * ke_fc + d * kd_fc * (1 - tax)

    return {
        "D/E": de, "Levered beta": beta_l, "Inflation-differential factor": fx,
        "rf_USD": rf_us, "rf_FC": rf_fc, "Spread_USD": spread_usd, "Spread_FC": spread_fc,
        "ke_USD": ke_usd, "kd_USD (pre-tax)": kd_usd, "kd_USD (after-tax)": kd_usd * (1 - tax),
        "WACC_USD": wacc_usd,
        "ke_FC": ke_fc, "kd_FC (pre-tax)": kd_fc, "kd_FC (after-tax)": kd_fc * (1 - tax),
        "WACC_FC": wacc_fc,
    }


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("🌍 International Cost of Capital")
st.caption("Cost of equity, cost of debt and WACC of a project in USD and in a selected country's currency.")

top = st.columns([1, 5])
if top[0].button("🔄 Refresh data", help="Clear cache and re-fetch all four sources"):
    st.cache_data.clear()
    st.rerun()

with st.spinner("Fetching data from Damodaran, OECD and Trading Economics..."):
    betas_df, betas_src, betas_ok = fetch_betas()
    bbb, bbb_src, bbb_ok = fetch_bbb_spread()
    tax_df, tax_src, tax_ok = fetch_tax_rates()

tax_map = dict(zip(tax_df["Country"], tax_df["Tax rate"]))
countries = sorted(TE_COUNTRIES)

industries = [i for i in betas_df["Industry Name"] if not i.lower().startswith("total market")]
beta_map = dict(zip(betas_df["Industry Name"], betas_df["Unlevered beta"]))

# ---------------- Sidebar inputs ----------------
sb = st.sidebar
sb.header("Project settings")
industry = sb.selectbox("Industry", industries,
                        index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
country = sb.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))
tax_choice = sb.radio("Project tax rate", ["U.S. tax rate", f"{country} tax rate"], index=0,
                      help="The same tax rate is used for both the USD and foreign-currency calculations.")
erp = sb.slider("Equity risk premium (%)", 0.0, 15.0, 6.5, 0.1) / 100
debt_share = sb.slider("Target debt share D/(D+E) (%)", 0.0, 90.0, 30.0, 1.0) / 100

sb.header("Manual overrides")
fetched_beta = float(beta_map.get(industry, float("nan")))
fetched_tax_us = float(tax_map.get("United States", float("nan")))
fetched_tax_fc = float(tax_map.get(country, float("nan")))
with st.spinner("Fetching 10Y yields from Trading Economics..."):
    fetched_rf_us, date_us, stat_us = fetch_yield("United States")
    fetched_rf_fc, date_fc, stat_fc = fetch_yield(country)
yld_ok = stat_us == "live" and stat_fc == "live"
yld_src = f"U.S.: {date_us or stat_us}" + ("" if country == "United States" else f" · {country}: {date_fc or stat_fc}") \
    + f" (fetched {now_utc()})"


def override(label, fetched, key, step=0.01, fmt="%.2f", scale=100.0):
    on = sb.toggle(f"Override {label}", key=f"on_{key}")
    if on:
        default = fetched * scale if pd.notna(fetched) else 0.0
        return sb.number_input(f"{label}", value=float(round(default, 4)), step=step, format=fmt,
                               key=f"val_{key}") / scale, True
    return fetched, False


beta_u, ov_beta = override("unlevered beta", fetched_beta, "beta", 0.01, "%.2f", 1.0)
tax_us, ov_tax_us = override("U.S. tax rate (%)", fetched_tax_us, "tax_us")
tax_fc, ov_tax_fc = override(f"{country} tax rate (%)", fetched_tax_fc, "tax_fc")
spread_usd, ov_spread = override("USD BBB spread (%)", bbb, "spread")
rf_us, ov_rf_us = override("U.S. 10Y yield (%)", fetched_rf_us, "rf_us")
rf_fc, ov_rf_fc = override(f"{country} 10Y yield (%)", fetched_rf_fc, "rf_fc")
spread_fc_manual, ov_spread_fc = override(f"{country}-currency BBB spread (%)", float("nan"), "spread_fc")
if not ov_spread_fc:
    spread_fc_manual = None

tax = tax_us if tax_choice.startswith("U.S.") else tax_fc

missing = [n for n, v in [("unlevered beta", beta_u), ("tax rate", tax), ("U.S. yield", rf_us),
                          (f"{country} yield", rf_fc), ("USD spread", spread_usd)] if pd.isna(v)]
if missing:
    st.error(f"Missing data for: {', '.join(missing)}. Please use the manual overrides in the sidebar.")
    st.stop()

res = compute(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_usd, spread_fc_manual)

# ---------------- Data source panel ----------------
with st.expander("📡 Data sources & dates", expanded=False):
    src = pd.DataFrame([
        ["Unlevered beta (U.S. industries)", "Aswath Damodaran - betas.xls", URL_BETAS, betas_src,
         "✅ live" if betas_ok else "⚠️ fallback"],
        ["BBB (Baa2) USD spread - large non-financial", "Aswath Damodaran - ratings.html", URL_RATINGS[0],
         bbb_src, "✅ live" if bbb_ok else "⚠️ fallback"],
        ["Corporate tax rates (combined statutory)", "OECD Corporate Tax Statistics", URL_OECD_PAGE,
         tax_src, "✅ live" if tax_ok else "⚠️ fallback"],
        ["10Y government bond yields", "Trading Economics", te_url(country), yld_src,
         "✅ live" if yld_ok else "⚠️ failed - use override"],
    ], columns=["Input", "Source", "URL", "Data date", "Status"])
    st.dataframe(src, hide_index=True, width="stretch",
                 column_config={"URL": st.column_config.LinkColumn("URL")})
    st.caption("OECD figures are retrieved from the OECD Data Explorer (dataset DSD_TAX_CIT@DF_CIT), which "
               "holds the data behind the Corporate Tax Statistics report linked above. "
               "If any source shows ⚠️ fallback, use the sidebar overrides with current values.")

# ---------------- Inputs used ----------------
st.subheader(f"{industry} · USD vs {country}")
c = st.columns(6)
c[0].metric("Unlevered beta", f"{beta_u:.2f}", "manual" if ov_beta else None, delta_color="off")
c[1].metric("Levered beta", f"{res['Levered beta']:.3f}")
c[2].metric(f"Tax rate ({'U.S.' if tax_choice.startswith('U.S.') else country})", f"{tax:.2%}")
c[3].metric("U.S. 10Y yield", f"{rf_us:.2%}", "manual" if ov_rf_us else None, delta_color="off")
c[4].metric(f"{country} 10Y yield", f"{rf_fc:.2%}", "manual" if ov_rf_fc else None, delta_color="off")
c[5].metric("USD BBB spread", f"{spread_usd:.2%}", "manual" if ov_spread else None, delta_color="off")

# ---------------- Side-by-side results ----------------
st.markdown("### Results")
L, R = st.columns(2)
with L:
    st.markdown("#### USD")
    m = st.columns(3)
    m[0].metric("Cost of equity", f"{res['ke_USD']:.2%}")
    m[1].metric("Cost of debt (pre-tax)", f"{res['kd_USD (pre-tax)']:.2%}")
    m[2].metric("WACC", f"{res['WACC_USD']:.2%}")
with R:
    st.markdown(f"#### {country} currency")
    m = st.columns(3)
    m[0].metric("Cost of equity", f"{res['ke_FC']:.2%}")
    m[1].metric("Cost of debt (pre-tax)", f"{res['kd_FC (pre-tax)']:.2%}")
    m[2].metric("WACC", f"{res['WACC_FC']:.2%}")

table = pd.DataFrame({
    "Item": ["Risk-free rate (10Y)", "Levered beta", "Equity risk premium", "Cost of equity",
             "BBB spread", "Cost of debt (pre-tax)", "Cost of debt (after-tax)", "Equity weight",
             "Debt weight", "Tax rate", "WACC"],
    "USD": [res["rf_USD"], res["Levered beta"], erp, res["ke_USD"], res["Spread_USD"],
            res["kd_USD (pre-tax)"], res["kd_USD (after-tax)"], 1 - debt_share, debt_share, tax, res["WACC_USD"]],
    f"{country} currency": [res["rf_FC"], res["Levered beta"], erp, res["ke_FC"], res["Spread_FC"],
                            res["kd_FC (pre-tax)"], res["kd_FC (after-tax)"], 1 - debt_share, debt_share, tax,
                            res["WACC_FC"]],
})


def fmt_cell(item, v):
    return f"{v:.3f}" if item == "Levered beta" else f"{v:.2%}"


disp = table.copy()
for col in disp.columns[1:]:
    disp[col] = [fmt_cell(i, v) for i, v in zip(table["Item"], table[col])]
st.dataframe(disp, hide_index=True, width="stretch")
st.caption(
    f"Foreign-currency BBB spread: **{'manual override' if ov_spread_fc else 'auto-calculated'}** "
    f"= {res['Spread_FC']:.2%}. Inflation-differential factor (1+rf_FC)/(1+rf_US) = "
    f"{res['Inflation-differential factor']:.4f}."
)

# ---------------- Tabs ----------------
tab1, tab2, tab3, tab4 = st.tabs(["📘 Assumptions & formulas", "🏭 All-industry comparison",
                                  "✅ Verification checks", "⬇️ Download"])

with tab1:
    st.markdown(r"""
**Step 1 - Relever the industry beta (Hamada)**
$$\beta_L = \beta_U\left[1 + (1-t)\frac{D}{E}\right],\qquad \frac{D}{E}=\frac{d}{1-d}$$

**Step 2 - USD cost of equity (CAPM)**
$$k_{e,\$} = r_{f,US} + \beta_L \times ERP$$

**Step 3 - USD cost of debt**
$$k_{d,\$} = r_{f,US} + s_{BBB,\$}$$

**Step 4 - Convert to the foreign currency (international Fisher / relative interest-rate parity)**
$$k_{FC} = (1+k_{\$})\frac{1+r_{f,FC}}{1+r_{f,US}} - 1$$
applied to both equity and debt. The implied foreign-currency spread is $s_{FC}=k_{d,FC}-r_{f,FC}$.
If the foreign-currency spread is overridden: $k_{d,FC}=r_{f,FC}+s_{FC}^{manual}$.

**Step 5 - WACC**
$$WACC = (1-d)\,k_e + d\,k_d\,(1-t)$$

**Main assumptions**
- The 10-year government yield is the risk-free rate in each currency; the yield gap reflects expected inflation differences (no sovereign default spread is added).
- One project tax rate, debt share and ERP are used for both currencies (toggle U.S. vs. selected-country tax rate in the sidebar).
- Industry betas are U.S. unlevered betas (Damodaran) - the project's business risk is assumed to be the same wherever it is valued.
- The USD borrowing spread is the Baa2/BBB spread for large non-financial firms (Damodaran).
- Tax rates are OECD combined (central + sub-central) statutory corporate income tax rates.
- With the FC spread override **off**, USD and foreign-currency WACCs are economically identical (one is just the other translated by the inflation differential).
""")

with tab2:
    st.markdown(f"Same settings (country **{country}**, tax {tax:.2%}, ERP {erp:.2%}, debt {debt_share:.0%}) "
                f"applied to every Damodaran industry.")
    rows = []
    for ind in industries:
        r_ = compute(float(beta_map[ind]), tax, debt_share, erp, rf_us, rf_fc, spread_usd, spread_fc_manual)
        rows.append({"Industry": ind, "Unlevered beta": float(beta_map[ind]), "Levered beta": r_["Levered beta"],
                     "ke USD": r_["ke_USD"], "WACC USD": r_["WACC_USD"],
                     f"ke {country}": r_["ke_FC"], f"WACC {country}": r_["WACC_FC"]})
    all_df = pd.DataFrame(rows).sort_values("WACC USD", ascending=False).reset_index(drop=True)
    pct_cols = [c_ for c_ in all_df.columns if c_.startswith(("ke", "WACC"))]
    st.dataframe(
        all_df.style.format({**{c_: "{:.2%}" for c_ in pct_cols}, "Unlevered beta": "{:.2f}", "Levered beta": "{:.3f}"})
        .apply(lambda s: ["background-color: #fff3b0" if v == industry else "" for v in s], subset=["Industry"]),
        hide_index=True, width="stretch", height=480)
    st.bar_chart(all_df.set_index("Industry")[["WACC USD", f"WACC {country}"]].head(25), horizontal=True)
    st.caption("Chart shows the 25 industries with the highest USD WACC.")

with tab3:
    st.markdown("Automatic sanity checks of the formulas using the current inputs:")
    eq = compute(beta_u, tax, debt_share, erp, rf_us, rf_us, spread_usd, None)
    z = compute(beta_u, tax, 0.0, erp, rf_us, rf_fc, spread_usd, spread_fc_manual)
    a = compute(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_usd, None)
    b = compute(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_usd, 0.05)
    checks = [
        ("Equal yields (rf_FC = rf_US, override off) -> equal WACC",
         f"{eq['WACC_USD']:.4%} vs {eq['WACC_FC']:.4%}", abs(eq["WACC_USD"] - eq["WACC_FC"]) < 1e-12),
        ("Equal yields -> equal cost of equity",
         f"{eq['ke_USD']:.4%} vs {eq['ke_FC']:.4%}", abs(eq["ke_USD"] - eq["ke_FC"]) < 1e-12),
        ("Zero debt -> WACC_USD = ke_USD",
         f"{z['WACC_USD']:.4%} vs {z['ke_USD']:.4%}", abs(z["WACC_USD"] - z["ke_USD"]) < 1e-12),
        ("Zero debt -> WACC_FC = ke_FC",
         f"{z['WACC_FC']:.4%} vs {z['ke_FC']:.4%}", abs(z["WACC_FC"] - z["ke_FC"]) < 1e-12),
        ("FC spread override (5%) leaves USD WACC unchanged",
         f"{a['WACC_USD']:.4%} vs {b['WACC_USD']:.4%}", abs(a["WACC_USD"] - b["WACC_USD"]) < 1e-12),
        ("FC spread override (5%) leaves USD ke unchanged",
         f"{a['ke_USD']:.4%} vs {b['ke_USD']:.4%}", abs(a["ke_USD"] - b["ke_USD"]) < 1e-12),
    ]
    # Class example (Colombia): bU 0.80, d 30%, t 35%, ERP 6.5%, rf_US 4%, rf_COP 12%, spread 1.5pp
    col = compute(0.80, 0.35, 0.30, 0.065, 0.04, 0.12, 0.015)
    for lab, key, target in [("Class example: WACC USD = 8.5265%", "WACC_USD", 0.085265),
                             ("Class example: ke COP = 19.16%", "ke_FC", 0.191600),
                             ("Class example: kd COP = 13.6154%", "kd_FC (pre-tax)", 0.136154),
                             ("Class example: WACC COP = 16.0670%", "WACC_FC", 0.160670)]:
        checks.append((lab, f"{col[key]:.4%}", abs(col[key] - target) < 5e-6))
    st.dataframe(pd.DataFrame([(n, v, "✅ PASS" if ok else "❌ FAIL") for n, v, ok in checks],
                              columns=["Check", "Values", "Result"]), hide_index=True, width="stretch")

with tab4:
    inputs = pd.DataFrame({
        "Parameter": ["Industry", "Target country", "Tax rate basis", "Tax rate", "Equity risk premium",
                      "Debt share", "Unlevered beta", "U.S. 10Y yield", f"{country} 10Y yield", "USD BBB spread",
                      "FC spread mode", "Generated"],
        "Value": [industry, country, tax_choice, f"{tax:.4%}", f"{erp:.4%}", f"{debt_share:.4%}", f"{beta_u:.4f}",
                  f"{rf_us:.4%}", f"{rf_fc:.4%}", f"{spread_usd:.4%}",
                  "manual" if ov_spread_fc else "auto", now_utc()],
    })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        inputs.to_excel(xw, sheet_name="Inputs", index=False)
        table.to_excel(xw, sheet_name="Results", index=False)
        all_df.to_excel(xw, sheet_name="All industries", index=False)
        src.to_excel(xw, sheet_name="Sources", index=False)
    d1, d2, d3 = st.columns(3)
    d1.download_button("Download results (Excel)", buf.getvalue(), "cost_of_capital_results.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("Download results (CSV)", pd.concat([inputs.rename(columns={"Value": "USD"}), table])
                       .to_csv(index=False).encode(), "cost_of_capital_results.csv", "text/csv")
    d3.download_button("Download all-industry table (CSV)", all_df.to_csv(index=False).encode(),
                       "all_industries.csv", "text/csv")

st.caption("Built for an International Finance course assignment. Formulas should be checked against the class notes.")
