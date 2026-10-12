"""Physics layer for the BESS module thermal & degradation simulator.

Runs a Doyle-Fuller-Newman (DFN) electrochemical model from PyBaMM with a
lumped thermal submodel and optional degradation submodels (solvent-
diffusion-limited SEI growth + irreversible lithium plating), driven by
BESS duty-cycle experiments.

Cell: LG M50, 5 Ah nominal, parameterised with the OKane2022 set — the
Chen2020 electrochemistry plus calibrated SEI/plating degradation
parameters (O'Kane et al. 2022) — representative of module-level BESS
design studies (Voltpack / Voltrack context).

This module is deliberately Streamlit-free so it can be unit-tested and
reused headlessly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pybamm

# Cell / module constants -------------------------------------------------

NOMINAL_CAPACITY_AH = 5.0  # LG M50
PARAMETER_SET = "OKane2022"  # Chen2020 cell + calibrated SEI/plating params
THERMAL_LIMIT_C = 60.0  # upper cell temperature limit drawn on charts
TEMP_WARN_C = 45.0  # KPI colour bands: green below, amber up to...
TEMP_DANGER_C = 55.0  # ...red above this

# Duty-cycle profiles ------------------------------------------------------

PROFILE_PULSE = "Grid Peak Shaving (Pulse)"
PROFILE_CONSTANT = "Constant High-Power Discharge"
PROFILE_FAST_CHARGE = "EV Fast Charge (CC-CV)"
PROFILES = (PROFILE_PULSE, PROFILE_CONSTANT, PROFILE_FAST_CHARGE)

C_RATES = (1.5, 2.0)

AMBIENT_MIN_C, AMBIENT_MAX_C = -10.0, 45.0
COOLING_MIN_H, COOLING_MAX_H = 5.0, 100.0

# Lifetime extrapolation assumptions
LIFETIME_YEARS = 10.0  # projection horizon
EOL_SOH = 0.80  # end-of-life state of health
CYCLES_PER_DAY = 1.0  # duty cycle repeats per day


@dataclass(frozen=True)
class SimConfig:
    """Immutable, hashable input bundle (safe as a cache key)."""

    profile: str = PROFILE_PULSE
    c_rate: float = 1.5  # only used by the constant-discharge profile
    ambient_temp_c: float = 25.0
    cooling_h: float = 30.0  # total heat transfer coefficient [W/m²K]
    degradation: bool = False

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"unknown profile {self.profile!r}; expected one of {PROFILES}")
        if self.profile == PROFILE_CONSTANT and self.c_rate not in C_RATES:
            raise ValueError(f"c_rate must be one of {C_RATES}, got {self.c_rate}")
        if not AMBIENT_MIN_C <= self.ambient_temp_c <= AMBIENT_MAX_C:
            raise ValueError(f"ambient_temp_c must be in [{AMBIENT_MIN_C}, {AMBIENT_MAX_C}]")
        if not COOLING_MIN_H <= self.cooling_h <= COOLING_MAX_H:
            raise ValueError(f"cooling_h must be in [{COOLING_MIN_H}, {COOLING_MAX_H}]")


@dataclass(frozen=True)
class SimResult:
    """Time series and KPIs; plain numpy so it pickles cleanly for caching."""

    config: SimConfig
    time_min: np.ndarray
    voltage_v: np.ndarray
    current_a: np.ndarray  # positive = discharge (PyBaMM experiment convention)
    temp_c: np.ndarray
    heat_w: np.ndarray
    soh: np.ndarray | None = None  # 1.0 = no fade; None when degradation off
    sei_loss_ah: np.ndarray | None = None  # cumulative, per mechanism;
    plating_loss_ah: np.ndarray | None = None  # None when degradation off
    kpis: dict = field(default_factory=dict)


def build_experiment(profile: str, c_rate: float = 1.5) -> tuple[pybamm.Experiment, float]:
    """Return (experiment, initial_soc) for each duty cycle.

    Initial SOC matters: charging at 2C from the Chen2020 default state
    (~90% SOC) is infeasible at t=0 and PyBaMM silently skips the step, so
    each profile pins a physically sensible starting point.
    """
    if profile == PROFILE_PULSE:
        # Two daily micro-cycles: evening peak discharge, rest, overnight recharge.
        experiment = pybamm.Experiment(
            [
                (
                    "Discharge at 1.5C for 15 minutes",
                    "Rest for 10 minutes",
                    "Charge at 1C for 20 minutes",
                )
            ]
            * 2
        )
        return experiment, 0.6
    if profile == PROFILE_CONSTANT:
        experiment = pybamm.Experiment([f"Discharge at {c_rate}C until 2.8 V"])
        return experiment, 0.8
    if profile == PROFILE_FAST_CHARGE:
        experiment = pybamm.Experiment(
            ["Charge at 2C until 4.2 V", "Hold at 4.2 V until C/20"]
        )
        return experiment, 0.15
    raise ValueError(f"unknown profile {profile!r}")


def run_simulation(config: SimConfig) -> SimResult:
    """Solve the DFN + lumped thermal model for the given duty cycle."""
    options: dict = {"thermal": "lumped"}
    if config.degradation:
        options["SEI"] = "solvent-diffusion limited"
        options["lithium plating"] = "irreversible"

    model = pybamm.lithium_ion.DFN(options=options)
    parameter_values = pybamm.ParameterValues(PARAMETER_SET)

    temp_k = config.ambient_temp_c + 273.15
    parameter_values.update(
        {
            "Ambient temperature [K]": temp_k,
            "Initial temperature [K]": temp_k,
            "Total heat transfer coefficient [W.m-2.K-1]": config.cooling_h,
        }
    )

    experiment, initial_soc = build_experiment(config.profile, config.c_rate)
    parameter_values.set_initial_state(initial_soc)

    sim = pybamm.Simulation(
        model, experiment=experiment, parameter_values=parameter_values
    )
    solution = sim.solve()

    time_min = solution["Time [min]"].entries
    voltage_v = solution["Terminal voltage [V]"].entries
    current_a = solution["Current [A]"].entries
    temp_c = solution["Volume-averaged cell temperature [C]"].entries
    heat_w = solution["Total heating [W]"].entries

    soh = sei_loss_ah = plating_loss_ah = None
    if config.degradation:
        sei_loss_ah = solution["Loss of capacity to negative SEI [A.h]"].entries
        plating_loss_ah = solution[
            "Loss of capacity to negative lithium plating [A.h]"
        ].entries
        soh = 1.0 - (sei_loss_ah + plating_loss_ah) / NOMINAL_CAPACITY_AH

    peak_temp_c = float(np.max(temp_c))
    kpis = {
        "peak_temp_c": peak_temp_c,
        "temp_rise_c": peak_temp_c - config.ambient_temp_c,
        "min_voltage_v": float(np.min(voltage_v)),
        "peak_heat_w": float(np.max(heat_w)),
        "peak_current_a": float(np.max(np.abs(current_a))),
        "duration_min": float(time_min[-1]),
        "final_soh": float(soh[-1]) if soh is not None else None,
        "final_sei_loss_ah": float(sei_loss_ah[-1]) if sei_loss_ah is not None else None,
        "final_plating_loss_ah": (
            float(plating_loss_ah[-1]) if plating_loss_ah is not None else None
        ),
        "exceeds_thermal_limit": peak_temp_c > THERMAL_LIMIT_C,
    }
    return SimResult(
        config=config,
        time_min=time_min,
        voltage_v=voltage_v,
        current_a=current_a,
        temp_c=temp_c,
        heat_w=heat_w,
        soh=soh,
        sei_loss_ah=sei_loss_ah,
        plating_loss_ah=plating_loss_ah,
        kpis=kpis,
    )


def project_lifetime(
    result: SimResult,
    years: float = LIFETIME_YEARS,
    cycles_per_day: float = CYCLES_PER_DAY,
    eol: float = EOL_SOH,
) -> dict:
    """First-order lifetime extrapolation from a single degradation run.

    Physics of the extrapolation: solvent-diffusion-limited SEI growth scales
    with sqrt(elapsed time) (it continues during rest, so calendar time is
    the driver), while irreversible lithium plating scales linearly with
    charge throughput (i.e. cycle count). Only the plating accumulated
    while actually charging is extrapolated — the plating variable also
    drifts slightly during discharge/initial relaxation, which is an
    artifact that linear extrapolation would amplify absurdly. Assumes the
    duty cycle repeats `cycles_per_day` times per day under constant
    conditions, with no feedback of fade on resistance. Honest talking
    point, not a warranty.
    """
    if result.kpis["final_sei_loss_ah"] is None:
        raise ValueError("project_lifetime needs a run with degradation=True")

    duty_h = result.kpis["duration_min"] / 60.0
    sei_per_duty = result.kpis["final_sei_loss_ah"]
    # plating accrued during charging steps only (current < 0 in PyBaMM)
    d_plating = np.diff(result.plating_loss_ah)
    plat_per_duty = float(d_plating[result.current_a[:-1] < 0].sum())

    t_years = np.linspace(0.0, years, 241)
    elapsed_h = t_years * 365.0 * 24.0
    cycles = t_years * 365.0 * cycles_per_day

    sei_ah = sei_per_duty * np.sqrt(elapsed_h / duty_h)
    plat_ah = plat_per_duty * cycles

    soh = 1.0 - (sei_ah + plat_ah) / NOMINAL_CAPACITY_AH
    below = np.nonzero(soh <= eol)[0]
    if below.size == 0:
        eol_years = None
    else:
        i = below[0]
        if i == 0:
            eol_years = 0.0
        else:  # linear interpolation on the crossing segment
            frac = (soh[i - 1] - eol) / (soh[i - 1] - soh[i])
            eol_years = float(t_years[i - 1] + frac * (t_years[i] - t_years[i - 1]))

    return {
        "time_years": t_years,
        "soh": soh,
        "soh_sei_only": 1.0 - sei_ah / NOMINAL_CAPACITY_AH,
        "soh_plating_only": 1.0 - plat_ah / NOMINAL_CAPACITY_AH,
        "eol_years": eol_years,
    }


def sweep_cooling(config: SimConfig, h_values=None) -> dict:
    """Peak cell temperature vs heat-transfer coefficient for a duty cycle.

    Solves the duty cycle at each h; degradation submodels are disabled
    inside the sweep because their effect on peak temperature is negligible
    and they roughly triple solve time. `h_required` is the interpolated
    smallest h that keeps the peak below THERMAL_LIMIT_C, or None when the
    limit is either never exceeded or unreachable even at h=100.
    """
    if h_values is None:
        h_values = np.geomspace(COOLING_MIN_H, COOLING_MAX_H, 8)
    h_values = np.asarray(h_values, dtype=float)

    peak_temps = np.array(
        [
            run_simulation(replace(config, cooling_h=float(h), degradation=False))
            .kpis["peak_temp_c"]
            for h in h_values
        ]
    )

    exceeds = bool(peak_temps.max() > THERMAL_LIMIT_C)
    h_required = None
    if exceeds and peak_temps.min() <= THERMAL_LIMIT_C:
        # peak(h) decreases monotonically; np.interp needs increasing x
        h_required = float(
            np.interp(THERMAL_LIMIT_C, peak_temps[::-1], h_values[::-1])
        )
    return {
        "h_values": h_values,
        "peak_temps_c": peak_temps,
        "exceeds_limit": exceeds,
        "h_required": h_required,
    }
