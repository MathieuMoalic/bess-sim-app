"""Streamlit UI for the BESS module thermal & degradation simulator.

All physics lives in bess_sim.py; this file only handles layout, controls
and rendering.
"""

import pathlib
import time

import numpy as np
import plotly.graph_objects as go
import streamlit as st

import bess_sim as bs

st.set_page_config(
    page_title="BESS Module Thermal & Degradation Simulator",
    page_icon="🔋",
    layout="wide",
)

# Colors used across charts and KPI cards.
COLORS = {
    "voltage": "#1f77b4",
    "current": "#ff7f0e",
    "temp": "#d62728",
    "soh": "#9467bd",
    "ambient": "#7f7f7f",
    "limit": "#8b0000",
    "ok": "#2e9e5b",
    "warn": "#d4a017",
    "danger": "#c0392b",
}

st.markdown(
    """
    <style>
      .kpi-card {
        border-radius: 10px; padding: 14px 18px; height: 100%;
        background: rgba(128, 128, 128, 0.06);
        border-left: 5px solid #7f7f7f;
      }
      .kpi-card.ok     { border-left-color: #2e9e5b; }
      .kpi-card.warn   { border-left-color: #d4a017; }
      .kpi-card.danger { border-left-color: #c0392b; }
      .kpi-label { font-size: 0.78rem; letter-spacing: 0.06em;
                   text-transform: uppercase; color: #6b7280; }
      .kpi-value { font-size: 1.9rem; font-weight: 700; margin: 2px 0; }
      .kpi-sub   { font-size: 0.82rem; color: #6b7280; }
      .status-pill {
        display: inline-block; border-radius: 999px; padding: 3px 12px;
        font-size: 0.85rem; font-weight: 600; color: white;
      }
      /* Circled-(i) hover tooltips on metrics and section headers. */
      .info-tip {
        display: inline-block; width: 17px; height: 17px; line-height: 17px;
        margin-left: 7px; text-align: center; border-radius: 50%;
        background: #9ca3af; color: #1f2937; font-size: 12px;
        font-weight: 700; font-style: italic; font-family: Georgia, serif;
        cursor: help; position: relative; vertical-align: middle;
      }
      .info-tip:hover .tip-body, .info-tip:focus .tip-body {
        visibility: visible; opacity: 1;
      }
      .tip-body {
        visibility: hidden; opacity: 0; transition: opacity 0.15s;
        position: absolute; z-index: 1000; width: 300px; left: 50%;
        transform: translateX(-50%); top: 24px;
        background: #111827; color: #f9fafb; padding: 12px 14px;
        border: 1px solid #4b5563;
        border-radius: 8px; font-size: 0.78rem; font-weight: 400;
        font-style: normal; line-height: 1.5; text-align: left;
        box-shadow: 0 8px 22px rgba(0, 0, 0, 0.45); white-space: normal;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def tip(text: str) -> str:
    """Circled-(i) with a hover popover; keyboard-focusable via tabindex."""
    return (
        f'<span class="info-tip" tabindex="0">i'
        f'<span class="tip-body">{text}</span></span>'
    )


# Plain-English explanations shown on hover (write for a non-engineer).
EXPLAIN = {
    "peak_temp": (
        "<b>The hottest the battery cell gets during this duty cycle.</b> "
        "Batteries age roughly twice as fast for every 10 °C hotter they run. "
        "Green means comfortable; amber means you are in derating territory "
        "(≈45–55 °C, where most BESS warranties start limiting power); above "
        "60 °C the separator — the safety membrane between the electrodes — "
        "begins to degrade and the risk of thermal runaway rises."
    ),
    "temp_rise": (
        "<b>How much hotter the cell runs than its surroundings.</b> This is "
        "the number the cooling system exists to control: every watt of "
        "energy that doesn't leave as electricity becomes heat (like a "
        "toaster element), and ΔT is what's left after cooling carries it "
        "away. Small ΔT = easier thermal design, cooler neighbors, longer "
        "battery life."
    ),
    "min_voltage": (
        "<b>The lowest the battery voltage sags while working.</b> Voltage "
        "sags when you draw current hard — like water pressure dropping when "
        "every shower in the building runs at once. The battery management "
        "system disconnects the load near 2.8 V to prevent damage; getting "
        "close to that means the cell is being pushed near its limit for how "
        "charged it currently is."
    ),
    "peak_heat": (
        "<b>The most heating power the cell produces at any moment, in "
        "watts.</b> This is what the cooling loop must be sized to remove. "
        "Most of it scales with the square of the current — double the "
        "current, four times the heat. Multiply by the number of cells in a "
        "module and you have the module's heat budget for the thermal "
        "engineer."
    ),
    "electrical": (
        "<b>Left axis: voltage — the 'pressure' pushing energy out. Right "
        "axis: current — the flow.</b> Positive current means the battery is "
        "discharging (giving energy), negative means charging. Watch voltage "
        "sag under load and recover during rest: that bounce is internal "
        "resistance. In the fast-charge profile, voltage is held at 4.2 V "
        "while the current tapers down — the industry-standard CC-CV recipe "
        "(fill fast, then gently)."
    ),
    "thermal": (
        "<b>Cell temperature over time.</b> Temperature follows the current "
        "with a delay, because the battery has thermal mass — like an oven "
        "that stays hot after you switch it off. Each current pulse makes "
        "heat (electrical resistance + chemical reactions); the cooling and "
        "the ambient air pull it back down. The dashed line at 60 °C is the "
        "safety ceiling a BESS design must never cross."
    ),
    "soh": (
        "<b>State of Health: the battery's remaining capacity as a % of when "
        "it was new</b> (100% = no wear). Two mechanisms eat it, and both are "
        "permanent: <b>SEI growth</b> — a thin film that slowly forms on the "
        "negative electrode and locks lithium away — and <b>lithium "
        "plating</b> — metallic lithium depositing in the wrong place during "
        "charging, which gets much worse when charging fast or cold. The "
        "right axis tracks each in milliamp-hours."
    ),
    "lifetime": (
        "<b>Extends the fade measured in one duty cycle out to 10 years</b> "
        "using each mechanism's own physics: SEI film growth follows a "
        "square-root-of-time law (it slows itself down as it thickens), "
        "while plating adds up in proportion to how much you charge. The 80% "
        "line is the industry's standard definition of 'end of life' — "
        "below it the battery is usually replaced or repurposed. A "
        "first-order estimate for comparing operating points, not a "
        "warranty."
    ),
    "sizing": (
        "<b>Each point answers: 'if cooling were this good, how hot would "
        "the cell get?'</b> The x-axis, h, measures cooling effectiveness in "
        "watts of heat removed per square metre per degree of temperature "
        "difference (W/m²K). The shaded bands are typical hardware: still "
        "air → fans → liquid cold plate. The dotted line marks the weakest "
        "cooling that still holds the 60 °C limit — that's the minimum loop "
        "you would have to build."
    ),
    "profile": (
        "The repeating job this battery does. <b>Peak shaving</b>: short "
        "bursts that smooth out grid demand spikes. <b>Constant "
        "discharge</b>: sustained maximum power delivery. <b>EV fast "
        "charge</b>: refill as quickly as the industry standard allows."
    ),
    "c_rate": (
        "<b>1C empties the battery in 1 hour; 2C empties it in 30 "
        "minutes.</b> Higher C-rate = more power delivered, but also more "
        "heat and more wear per unit of energy."
    ),
    "ambient": (
        "The temperature of the air or coolant around the module. A desert "
        "rooftop in summer is ≈45 °C; an unheated enclosure in a cold "
        "climate can reach −10 °C. Everything the battery does is affected "
        "by this starting point."
    ),
}


# cache_data pickles results, so a SimResult pickled by an older bess_sim.py
# would crash the UI; keying the cache on the module's mtime invalidates it
# whenever the physics layer changes.
_PHYSICS_VERSION = str(pathlib.Path(bs.__file__).stat().st_mtime)


@st.cache_data(show_spinner=False)
def cached_run(config: bs.SimConfig, _physics_version: str) -> bs.SimResult:
    return bs.run_simulation(config)


# One-click demo scenarios. Each writes widget state via st.session_state;
# the buttons render above the controls, so values apply on the same run.
PRESETS: dict[str, dict] = {
    "🌱 Baseline — temperate grid duty": {
        "set": {
            "profile": bs.PROFILE_PULSE,
            "ambient_temp_c": 25,
            "cooling_h": 30,
            "degradation": False,
        },
        "help": "Grid peak shaving at 25 °C, forced-air cooling (h=30). "
        "~9 °C rise, all green.",
    },
    "🏜️ Desert heat + passive cooling": {
        "set": {
            "profile": bs.PROFILE_PULSE,
            "ambient_temp_c": 45,
            "cooling_h": 5,
            "degradation": False,
        },
        "help": "45 °C ambient, natural convection only (h=5). The cell "
        "sails past the 60 °C limit — the case for liquid cooling.",
    },
    "💧 Desert + liquid cold plate": {
        "set": {
            "profile": bs.PROFILE_PULSE,
            "ambient_temp_c": 45,
            "cooling_h": 100,
            "degradation": False,
        },
        "help": "Same desert day with an active liquid loop (h=100): "
        "temperature pins near ambient.",
    },
    "⚡ Room-temp fast charge": {
        "set": {
            "profile": bs.PROFILE_FAST_CHARGE,
            "ambient_temp_c": 25,
            "cooling_h": 30,
            "degradation": True,
        },
        "help": "2C CC-CV at 25 °C with degradation on: plating already "
        "outpaces SEI — roughly 2% capacity lost in one charge.",
    },
    "🧊 Arctic fast charge (plating)": {
        "set": {
            "profile": bs.PROFILE_FAST_CHARGE,
            "ambient_temp_c": -10,
            "cooling_h": 30,
            "degradation": True,
        },
        "help": "2C CC-CV at −10 °C: plating dominates with ~4× the "
        "capacity loss of the room-temperature charge — the pre-heating "
        "and charge-derating argument.",
    },
}

# --- Sidebar controls -------------------------------------------------------

st.sidebar.header("⚙️ Simulation Controls")

st.sidebar.subheader("🎬 Demo scenarios")
for label, preset in PRESETS.items():
    if st.sidebar.button(label, help=preset["help"], width="stretch"):
        for key, value in preset["set"].items():
            st.session_state[key] = value

profile = st.sidebar.selectbox(
    "Operating Duty Cycle", bs.PROFILES, key="profile", help=EXPLAIN["profile"]
)

c_rate = 1.5
if profile == bs.PROFILE_CONSTANT:
    c_rate = st.sidebar.radio(
        "Discharge C-rate",
        bs.C_RATES,
        format_func=lambda c: f"{c:g}C ({c * bs.NOMINAL_CAPACITY_AH:g} A)",
        help=EXPLAIN["c_rate"],
    )

ambient_temp_c = st.sidebar.slider(
    "Ambient temperature (°C)",
    int(bs.AMBIENT_MIN_C),
    int(bs.AMBIENT_MAX_C),
    25,
    help=EXPLAIN["ambient"],
    key="ambient_temp_c",
)

cooling_h = st.sidebar.slider(
    "Cooling coefficient h (W/m²K)",
    int(bs.COOLING_MIN_H),
    int(bs.COOLING_MAX_H),
    30,
    step=5,
    help="5 ≈ natural convection · 30–50 ≈ forced air · 100 ≈ active liquid cooling loop.",
    key="cooling_h",
)

enable_degradation = st.sidebar.checkbox(
    "Degradation models (SEI + Li plating)",
    value=False,
    help="Adds solvent-diffusion-limited SEI growth and irreversible lithium "
    "plating → capacity fade. Try fast charge at -10 °C and watch plating "
    "dominate.",
    key="degradation",
)

config = bs.SimConfig(
    profile=profile,
    c_rate=float(c_rate),
    ambient_temp_c=float(ambient_temp_c),
    cooling_h=float(cooling_h),
    degradation=enable_degradation,
)

# Flag the preset (if any) the current controls match exactly.
_active = next(
    (
        label
        for label, preset in PRESETS.items()
        if config.profile == preset["set"]["profile"]
        and config.ambient_temp_c == float(preset["set"]["ambient_temp_c"])
        and config.cooling_h == float(preset["set"]["cooling_h"])
        and config.degradation == preset["set"]["degradation"]
    ),
    None,
)
if _active:
    st.sidebar.success(f"▶ Active: {_active}")
else:
    st.sidebar.caption("Custom parameter set")

with st.sidebar.expander("🧪 Model & assumptions"):
    st.markdown(
        f"""
        **Model:** Doyle-Fuller-Newman (DFN) electrochemistry
        with **lumped thermal** submodel.

        **Cell:** LG M50, {bs.NOMINAL_CAPACITY_AH:g} Ah nominal
        (OKane2022 parameters — Chen2020 electrochemistry plus calibrated
        SEI / lithium-plating degradation).

        **Lumped energy balance:**
        $m\\,c_p\\,\\dot{{T}} = Q_{{gen}} - hA\\,(T - T_{{amb}})$

        **Thermal limit:** {bs.THERMAL_LIMIT_C:.0f} °C
        (derating typically starts near {bs.TEMP_WARN_C:.0f} °C).
        """
    )

# --- Header & run -----------------------------------------------------------

st.title("🔋 BESS Module Thermal & Degradation Simulator")
st.caption(
    "Physics-based battery modeling for stationary storage — electrochemistry "
    "(PyBaMM DFN), lumped thermal management, and dynamic duty-cycle response "
    "in a Voltpack / Voltrack-sized design context."
)

try:
    t0 = time.perf_counter()
    with st.spinner("Solving coupled electrochemical–thermal ODE/PDE system…"):
        result = cached_run(config, _PHYSICS_VERSION)
    solve_time_s = time.perf_counter() - t0
except Exception as e:  # noqa: BLE001 — surface any solver failure to the user
    st.error(f"Simulation failed: {e}")
    st.stop()

k = result.kpis
state = (
    "danger" if k["peak_temp_c"] > bs.TEMP_DANGER_C
    else "warn" if k["peak_temp_c"] > bs.TEMP_WARN_C
    else "ok"
)
pill_color = COLORS["danger"] if k["exceeds_thermal_limit"] else COLORS["ok"]
pill_text = (
    "Thermal limit exceeded" if k["exceeds_thermal_limit"]
    else "Within thermal envelope"
)
pills = (
    f'<span class="status-pill" style="background:{pill_color}" '
    f'title="The 60 °C safety ceiling was never crossed during this run.">'
    f"{pill_text}</span>"
)
if k["final_plating_loss_ah"] is not None and k["final_plating_loss_ah"] * 1000 > 10:
    pills += (
        f'<span class="status-pill" style="background:{COLORS["danger"]};margin-left:6px" '
        f'title="Metallic lithium deposited during charging — permanent capacity loss.">'
        f"⚠ Li plating: {k['final_plating_loss_ah'] * 1000:.0f} mAh lost</span>"
    )
pills += (
    f'<span class="status-pill" style="background:#4b5563;margin-left:6px">'
    f"DFN · OKane2022 · {k['duration_min']:.0f} min duty cycle · solved in {solve_time_s:.1f} s"
    f"</span>"
)
st.markdown(pills, unsafe_allow_html=True)

# --- KPI cards --------------------------------------------------------------


def kpi_card(label: str, value: str, sub: str, state: str = "", explain: str = "") -> None:
    info = tip(explain) if explain else ""
    st.markdown(
        f'<div class="kpi-card {state}"><div class="kpi-label">{label}{info}</div>'
        f'<div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )


c1, c2, c3, c4 = st.columns(4)
with c1:
    kpi_card(
        "Peak cell temperature",
        f"{k['peak_temp_c']:.1f} °C",
        f"+{k['temp_rise_c']:.1f} °C above ambient",
        state=state,
        explain=EXPLAIN["peak_temp"],
    )
with c2:
    kpi_card(
        "Max temperature rise ΔT",
        f"+{k['temp_rise_c']:.1f} °C",
        f"Ambient {config.ambient_temp_c:.0f} °C · h = {config.cooling_h:.0f} W/m²K",
        explain=EXPLAIN["temp_rise"],
    )
with c3:
    v_state = "danger" if k["min_voltage_v"] < 2.8 else "ok"
    kpi_card(
        "Minimum terminal voltage",
        f"{k['min_voltage_v']:.2f} V",
        "Instantaneous minimum under load (2.8 V cut-off)",
        state=v_state,
        explain=EXPLAIN["min_voltage"],
    )
with c4:
    kpi_card(
        "Peak heat generation",
        f"{k['peak_heat_w']:.2f} W",
        f"Peak current {k['peak_current_a']:.1f} A per cell",
        explain=EXPLAIN["peak_heat"],
    )

# --- Charts -----------------------------------------------------------------

t = result.time_min

st.markdown(
    f"### 📈 Electrical response — voltage & current {tip(EXPLAIN['electrical'])}",
    unsafe_allow_html=True,
)
fig_e = go.Figure()
fig_e.add_trace(
    go.Scatter(
        x=t, y=result.voltage_v, name="Terminal voltage (V)",
        line=dict(color=COLORS["voltage"], width=2),
    )
)
fig_e.add_trace(
    go.Scatter(
        x=t, y=result.current_a, name="Current (A) · + = discharge",
        yaxis="y2", line=dict(color=COLORS["current"], width=2),
    )
)
fig_e.update_layout(
    template="plotly_white",
    hovermode="x unified",
    xaxis_title="Time (min)",
    yaxis=dict(title="Voltage (V)", gridcolor="#e5e7eb"),
    yaxis2=dict(
        title="Current (A)", overlaying="y", side="right",
        gridcolor="#f3d9b5",
    ),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    margin=dict(l=10, r=10, t=30, b=10),
    height=380,
)
st.plotly_chart(fig_e, width="stretch")

st.markdown(
    f"### 🌡️ Thermal response — cell temperature {tip(EXPLAIN['thermal'])}",
    unsafe_allow_html=True,
)
fig_t = go.Figure()
fig_t.add_trace(
    go.Scatter(
        x=t, y=result.temp_c, name="Cell temperature (°C)",
        line=dict(color=COLORS["temp"], width=2.5),
        fill="tozeroy", fillcolor="rgba(214,39,40,0.06)",
    )
)
fig_t.add_trace(
    go.Scatter(
        x=[t[0], t[-1]], y=[config.ambient_temp_c] * 2,
        name=f"Ambient ({config.ambient_temp_c:.0f} °C)",
        line=dict(color=COLORS["ambient"], width=1.5, dash="dot"),
    )
)
fig_t.add_trace(
    go.Scatter(
        x=[t[0], t[-1]], y=[bs.THERMAL_LIMIT_C] * 2,
        name=f"Thermal limit ({bs.THERMAL_LIMIT_C:.0f} °C)",
        line=dict(color=COLORS["limit"], width=2, dash="dash"),
    )
)
fig_t.update_layout(
    template="plotly_white",
    hovermode="x unified",
    xaxis_title="Time (min)",
    yaxis_title="Temperature (°C)",

    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    margin=dict(l=10, r=10, t=30, b=10),
    height=380,
)
st.plotly_chart(fig_t, width="stretch")

# --- Cooling-loop sizing -----------------------------------------------------

st.markdown(
    f"### ❄️ Cooling-loop sizing {tip(EXPLAIN['sizing'])}",
    unsafe_allow_html=True,
)
st.caption(
    "Peak cell temperature vs cooling coefficient for the current duty cycle "
    f"and {config.ambient_temp_c:.0f} °C ambient. The sweep re-solves the "
    "thermal model without degradation submodels (negligible effect on peak "
    "temperature) and is cached after the first run."
)


@st.cache_data(show_spinner=False)
def cached_sweep(profile: str, c_rate: float, ambient_temp_c: float, _v: str) -> dict:
    cfg = bs.SimConfig(
        profile=profile, c_rate=c_rate, ambient_temp_c=ambient_temp_c,
        cooling_h=30.0, degradation=False,
    )
    return bs.sweep_cooling(cfg)


if st.button("🔍 Compute sizing curve (~10 s)", key="sweep_btn"):
    st.session_state["show_sweep"] = True

if st.session_state.get("show_sweep"):
    with st.spinner("Sweeping heat-transfer coefficient 5 → 100 W/m²K…"):
        sweep = cached_sweep(
            config.profile, config.c_rate, config.ambient_temp_c, _PHYSICS_VERSION
        )

    fig_c = go.Figure()
    for x0, x1, label, color in (
        (5, 10, "natural convection", "#f59e0b"),
        (10, 50, "forced air", "#3b82f6"),
        (50, 100, "liquid loop", "#06b6d4"),
    ):
        fig_c.add_vrect(
            x0=x0, x1=x1, fillcolor=color, opacity=0.06, line_width=0,
            annotation_text=label, annotation_position="top",
            annotation=dict(font_size=10, font_color=color),
        )
    fig_c.add_trace(
        go.Scatter(
            x=sweep["h_values"], y=sweep["peak_temps_c"],
            name="Peak cell temperature (°C)", mode="lines+markers",
            line=dict(color=COLORS["temp"], width=2.5),
        )
    )
    fig_c.add_hline(
        y=bs.THERMAL_LIMIT_C, line=dict(color=COLORS["limit"], width=2, dash="dash"),
        annotation_text=f"thermal limit {bs.THERMAL_LIMIT_C:.0f} °C",
        annotation=dict(font_color=COLORS["limit"]),
    )
    peak_at_current = float(
        np.interp(config.cooling_h, sweep["h_values"], sweep["peak_temps_c"])
    )
    fig_c.add_trace(
        go.Scatter(
            x=[config.cooling_h], y=[peak_at_current], mode="markers+text",
            marker=dict(symbol="star", size=16, color=COLORS["current"],
                        line=dict(width=1.5, color="white")),
            text=[f"current h = {config.cooling_h:.0f}"], textposition="bottom right",
            name="Current operating point",
        )
    )
    if sweep["h_required"] is not None:
        fig_c.add_vline(
            x=sweep["h_required"], line=dict(color=COLORS["ok"], width=2, dash="dot"),
            annotation_text=f"h ≥ {sweep['h_required']:.0f} required",
            annotation=dict(font_color=COLORS["ok"]),
        )
    fig_c.update_layout(
        template="plotly_white",
        xaxis=dict(title="Total heat transfer coefficient h (W/m²K)", type="log",
                   tickvals=[5, 10, 20, 50, 100]),
        yaxis_title="Peak cell temperature (°C)",
        showlegend=False,
        margin=dict(l=10, r=10, t=30, b=10),
        height=380,
    )
    st.plotly_chart(fig_c, width="stretch")

    if sweep["h_required"] is not None:
        st.success(
            f"**Sizing answer:** the loop must deliver h ≥ "
            f"**{sweep['h_required']:.0f} W/m²K** to hold the peak below "
            f"{bs.THERMAL_LIMIT_C:.0f} °C for this duty cycle at "
            f"{config.ambient_temp_c:.0f} °C ambient "
            f"(current setting: h = {config.cooling_h:.0f})."
        )
    elif sweep["exceeds_limit"]:
        st.error(
            "Even h = 100 W/m²K (aggressive liquid cooling) cannot hold the "
            "thermal limit for this duty cycle at this ambient — derate the "
            "current, pre-cool, or add thermal mass."
        )
    else:
        st.success(
            f"Thermal limit is maintained across the whole range: even natural "
            f"convection (h = {bs.COOLING_MIN_H:.0f} W/m²K) keeps the peak at "
            f"{sweep['peak_temps_c'][0]:.1f} °C for this duty cycle."
        )

if result.soh is not None:
    st.markdown(
        f"### 📉 State of health — SEI & lithium-plating capacity fade "
        f"{tip(EXPLAIN['soh'])}",
        unsafe_allow_html=True,
    )
    fig_s = go.Figure()
    fig_s.add_trace(
        go.Scatter(
            x=t, y=result.soh * 100.0, name="SoH (%)",
            line=dict(color=COLORS["soh"], width=2.5),
        )
    )
    fig_s.add_trace(
        go.Scatter(
            x=t, y=result.sei_loss_ah * 1000.0, name="Loss to SEI (mAh)",
            yaxis="y2", line=dict(color=COLORS["voltage"], width=1.8, dash="dot"),
        )
    )
    fig_s.add_trace(
        go.Scatter(
            x=t, y=result.plating_loss_ah * 1000.0, name="Loss to Li plating (mAh)",
            yaxis="y2", line=dict(color=COLORS["danger"], width=2),
        )
    )
    fig_s.update_layout(
        template="plotly_white",
        hovermode="x unified",
        xaxis_title="Time (min)",
        yaxis=dict(title="State of health (%)", tickformat=".2f"),
        yaxis2=dict(
            title="Cumulative loss (mAh)", overlaying="y", side="right",
            showgrid=False,
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=30, b=10),
        height=340,
    )
    st.plotly_chart(fig_s, width="stretch")

    sei_mah = result.kpis["final_sei_loss_ah"] * 1000
    plat_mah = result.kpis["final_plating_loss_ah"] * 1000
    fade_pct = (1.0 - result.soh[-1]) * 100
    if plat_mah > 10:
        st.warning(
            f"⚠️ **Lithium plating dominated this duty cycle:** {plat_mah:.0f} mAh "
            f"({plat_mah / bs.NOMINAL_CAPACITY_AH * 1000:.2f}% of nameplate) plated "
            f"irreversibly vs {sei_mah:.1f} mAh to SEI — {fade_pct:.2f}% SoH in a "
            f"single cycle. Plating kicks in when the anode potential approaches "
            f"0 V vs Li/Li⁺; reduce charge rate or pre-warm the cell "
            f"(keep it above ~10 °C)."
        )
    else:
        st.info(
            f"Capacity lost over this duty cycle: {fade_pct:.4f}% "
            f"({sei_mah:.2f} mAh SEI · {plat_mah:.2f} mAh plating). "
            "SEI grows as √t and roughly doubles every ~10 °C; plating appears "
            "when charging fast or cold — try EV Fast Charge at −10 °C ambient."
        )

    # --- Lifetime extrapolation -------------------------------------------

    st.markdown(
        f"### 📅 Lifetime projection (first-order) {tip(EXPLAIN['lifetime'])}",
        unsafe_allow_html=True,
    )
    proj = bs.project_lifetime(result)

    fig_l = go.Figure()
    fig_l.add_trace(
        go.Scatter(
            x=proj["time_years"], y=proj["soh"] * 100.0, name="Total SoH (%)",
            line=dict(color=COLORS["soh"], width=2.5),
        )
    )
    fig_l.add_trace(
        go.Scatter(
            x=proj["time_years"], y=proj["soh_plating_only"] * 100.0,
            name="Plating only (%)",
            line=dict(color=COLORS["danger"], width=1.6, dash="dash"),
        )
    )
    fig_l.add_trace(
        go.Scatter(
            x=proj["time_years"], y=proj["soh_sei_only"] * 100.0,
            name="SEI only (%)",
            line=dict(color=COLORS["voltage"], width=1.6, dash="dot"),
        )
    )
    fig_l.add_hline(
        y=bs.EOL_SOH * 100.0, line=dict(color=COLORS["limit"], width=2, dash="dash"),
        annotation_text=f"EOL ({bs.EOL_SOH * 100:.0f}% SoH)",
        annotation=dict(font_color=COLORS["limit"]),
    )
    if proj["eol_years"] is not None:
        fig_l.add_trace(
            go.Scatter(
                x=[proj["eol_years"]], y=[bs.EOL_SOH * 100.0], mode="markers",
                marker=dict(symbol="x", size=12, color=COLORS["danger"]),
                name="EOL crossing",
            )
        )
    fig_l.update_layout(
        template="plotly_white",
        hovermode="x unified",
        xaxis_title="Years of operation",
        yaxis=dict(
            title="State of health (%)",
            tickformat=".0f",
            # scale the window to the actual fade so mild cases stay readable
            range=[max(0.0, proj["soh"].min() - 0.02) * 100, 100.5],
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=30, b=10),
        height=340,
    )
    st.plotly_chart(fig_l, width="stretch")

    if proj["eol_years"] is not None:
        eol = proj["eol_years"]
        eol_text = f"{eol * 365:.0f} days" if eol < 2 else f"{eol:.1f} years"
        st.error(
            f"**EOL ({bs.EOL_SOH * 100:.0f}% SoH) after {eol_text}** of this duty "
            f"cycle — dominated by {'lithium plating' if proj['soh_plating_only'][-1] < proj['soh_sei_only'][-1] else 'SEI growth'}. "
            "This operating point is not lifetime-viable; derate the charge "
            "rate, pre-warm the cell, or accept replacement cost."
        )
    else:
        st.success(
            f"No EOL within {bs.LIFETIME_YEARS:.0f} years under these assumptions "
            f"(SoH ≥ {proj['soh'][-1] * 100:.1f}% at {bs.LIFETIME_YEARS:.0f} years)."
        )
    st.caption(
        "First-order extrapolation from one duty cycle: SEI ∝ √(calendar time), "
        "plating ∝ charge throughput, duty repeats 1×/day under constant "
        "conditions, no fade→resistance feedback. Directionally sound for "
        "operating-point comparisons — not a lifetime warranty."
    )
else:
    st.caption(
        "Enable the degradation models in the sidebar to add the "
        "state-of-health chart (SEI + lithium plating)."
    )

# --- Explain-it-like-I'm-five -----------------------------------------------

with st.expander("🔤 Plain-English glossary (new to batteries? start here)"):
    st.markdown(
        """
| Term | What it actually means |
|---|---|
| **C-rate** | Speed of charging/discharging. 1C empties the battery in 1 hour, 2C in 30 minutes. More speed = more heat and wear. |
| **CC-CV** | The standard fast-charging recipe: hold a strong **C**onstant **C**urrent until the battery hits 4.2 V, then hold that **C**onstant **V**oltage while the current gently tapers off. |
| **SoH — State of Health** | The battery's remaining capacity as a % of brand-new. Like a phone that only lasts 80% as long as when it was new. |
| **EOL — End of Life** | The industry's replacement threshold: 80% SoH. Below it, the battery is typically swapped or repurposed. |
| **SEI growth** | A film that slowly forms inside the battery on the negative electrode and permanently traps some lithium — the steady, inevitable part of aging. Grows like √time and speeds up with heat. |
| **Lithium plating** | Metallic lithium deposited in the wrong place during charging. Permanent loss, and in the worst case it grows dendrites that can pierce the separator and short the cell. Triggered by fast charging, cold cells, or charging when nearly full. |
| **Voltage sag** | The momentary voltage drop under load (like water pressure dropping when every tap runs). Recovers when the load stops. |
| **ΔT (temperature rise)** | How much hotter the battery runs than its surroundings. The cooling system's whole job is keeping this small. |
| **h (W/m²K)** | Cooling effectiveness: watts of heat removed per square metre of surface, per degree of temperature difference. ~5 = still air, ~30 = fans, ~100 = liquid cold plate. |
| **Lumped thermal model** | Treats the cell as one uniform temperature — one number instead of a 3D map. Fast and accurate for small cells; a real module adds cell-to-cell spread. |
| **DFN model** | The "gold standard" physics simulation of a lithium-ion cell (Doyle-Fuller-Newman): tracks lithium concentrations, potentials and reactions through the electrodes and electrolyte. |
| **Thermal runaway** | The failure mode where heat accelerates the reactions that make more heat. The 60 °C ceiling exists to stay far away from it. |
"""
    )

# --- Interview talking points ----------------------------------------------

with st.expander("🎓 Physics notes for the walk-through"):
    st.markdown(
        f"""
        **Electrochemistry — DFN (pseudo-2D) model.** Porous electrode theory
        with solid-phase Li diffusion, electrolyte concentration/potential and
        Butler-Volmer kinetics in both electrodes; solved by PyBaMM's
        CasADi/IDAEs back-end in ~1 s per duty cycle.

        **Thermal — lumped single-node.**
        $m\\,c_p\\,\\dot{{T}} = Q_{{ohm}} + Q_{{rxn}} + Q_{{rev}} - hA\\,(T - T_{{amb}})$
        Heat generation comes straight from the electrochemical solution, so
        the temperature trace responds to polarisation losses, not a lookup
        table. The h slider spans natural convection (~5 W/m²K) to an active
        liquid cold plate (~100 W/m²K).

        **Degradation — SEI + lithium plating.** Solvent-diffusion-limited
        SEI formation on the negative electrode consumes cyclable lithium;
        fade grows as $\\sqrt{{t}}$ and roughly doubles every ~10 °C
        (Arrhenius). Irreversible lithium plating switches on when the anode
        potential is driven toward 0 V vs Li/Li⁺ — fast charge, high SOC or a
        cold cell. Plated lithium is dead capacity and, in the worst case,
        dendrite growth → separator puncture → short circuit.

        **Module-level scaling.** Per-cell results scale to a
        Voltpack-sized module by s×p grouping; the quantities that drive
        design decisions are ΔT above ambient and peak heat load per cell —
        exactly the two KPI cards above.
        """
    )
