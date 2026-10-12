"""Physics-layer tests: every duty cycle must solve, and the thermal/
degradation response must move in the physically correct direction."""

import numpy as np
import pytest

import bess_sim as bs


def test_config_validation():
    with pytest.raises(ValueError):
        bs.SimConfig(profile="Bogus Profile")
    with pytest.raises(ValueError):
        bs.SimConfig(ambient_temp_c=60)
    with pytest.raises(ValueError):
        bs.SimConfig(cooling_h=500)
    with pytest.raises(ValueError):
        bs.SimConfig(profile=bs.PROFILE_CONSTANT, c_rate=3.0)


def test_pulse_solves_and_stays_in_bounds():
    result = bs.run_simulation(bs.SimConfig(profile=bs.PROFILE_PULSE))
    n = result.time_min.size
    assert n > 10
    assert all(
        arr.shape == (n,) for arr in (result.voltage_v, result.current_a, result.temp_c, result.heat_w)
    )
    assert np.all(np.isfinite(result.voltage_v))
    # 90-minute schedule: 2 x (15 + 10 + 20) min
    assert result.kpis["duration_min"] == pytest.approx(90.0, abs=1.0)
    assert 2.5 <= result.kpis["min_voltage_v"] <= 4.6
    assert result.kpis["peak_temp_c"] >= result.config.ambient_temp_c
    assert result.soh is None


def test_current_sign_convention_discharge_positive():
    """The UI labels current 'positive = discharge'; lock that convention."""
    result = bs.run_simulation(bs.SimConfig(profile=bs.PROFILE_CONSTANT))
    # Nearly the whole run is discharge, so the mean must be positive.
    assert np.mean(result.current_a) > 0
    assert result.kpis["peak_current_a"] == pytest.approx(7.5, abs=0.5)  # 1.5C x 5 Ah


@pytest.mark.parametrize("c_rate", bs.C_RATES)
def test_constant_discharge_c_rates(c_rate):
    result = bs.run_simulation(
        bs.SimConfig(profile=bs.PROFILE_CONSTANT, c_rate=c_rate)
    )
    assert result.kpis["peak_current_a"] == pytest.approx(c_rate * bs.NOMINAL_CAPACITY_AH, abs=0.5)
    # Voltage-controlled termination: never far below the 2.8 V cut-off.
    assert result.kpis["min_voltage_v"] >= 2.7


def test_fast_charge_completes_cccv():
    result = bs.run_simulation(bs.SimConfig(profile=bs.PROFILE_FAST_CHARGE))
    # If the 2C step were skipped as infeasible, the run would be ~0 min long.
    assert result.kpis["duration_min"] > 10
    assert result.kpis["min_voltage_v"] > 3.0  # starts at 15% SOC, charging
    assert np.max(result.voltage_v) == pytest.approx(4.2, abs=0.05)  # CV hold
    assert np.mean(result.current_a) < 0  # charge = negative current


def test_stronger_cooling_lowers_peak_temperature():
    hot = bs.SimConfig(profile=bs.PROFILE_PULSE, ambient_temp_c=45, cooling_h=5)
    cooled = bs.SimConfig(profile=bs.PROFILE_PULSE, ambient_temp_c=45, cooling_h=100)
    t_passive = bs.run_simulation(hot).kpis["peak_temp_c"]
    t_liquid = bs.run_simulation(cooled).kpis["peak_temp_c"]
    assert t_liquid < t_passive
    # The passive + hot corner is the demo's danger case: expect a big rise.
    assert t_passive - 45.0 > 10.0


def test_hotter_ambient_means_hotter_cell():
    mild = bs.run_simulation(bs.SimConfig(ambient_temp_c=25))
    hot = bs.run_simulation(bs.SimConfig(ambient_temp_c=45))
    assert hot.kpis["peak_temp_c"] > mild.kpis["peak_temp_c"]


def test_degradation_tracks_capacity_loss_by_mechanism():
    degraded = bs.run_simulation(bs.SimConfig(profile=bs.PROFILE_PULSE, degradation=True))
    pristine = bs.run_simulation(bs.SimConfig(profile=bs.PROFILE_PULSE, degradation=False))

    assert degraded.soh is not None and degraded.sei_loss_ah is not None
    assert np.all(np.diff(degraded.soh) <= 1e-12)  # monotonically fading
    assert degraded.soh[-1] < degraded.soh[0]
    # One 90-minute cycle is a tiny fraction of life: fade present but small.
    assert 0.95 < degraded.soh[-1] < 1.0
    assert degraded.kpis["final_soh"] == pytest.approx(degraded.soh[-1])
    for loss in (degraded.sei_loss_ah, degraded.plating_loss_ah):
        assert np.all(np.diff(loss) >= -1e-15)  # irreversible: never recovers
    for pristine_loss in (pristine.soh, pristine.sei_loss_ah, pristine.plating_loss_ah):
        assert pristine_loss is None


def test_cold_fast_charge_plates_lithium():
    """The headline degradation story: charging a cold cell plates lithium."""
    warm = bs.run_simulation(
        bs.SimConfig(profile=bs.PROFILE_FAST_CHARGE, ambient_temp_c=25, degradation=True)
    )
    cold = bs.run_simulation(
        bs.SimConfig(profile=bs.PROFILE_FAST_CHARGE, ambient_temp_c=-10, degradation=True)
    )
    warm_plating_ah = warm.kpis["final_plating_loss_ah"]
    cold_plating_ah = cold.kpis["final_plating_loss_ah"]
    # Plating dominates SEI on fast charge at room temperature already...
    assert warm_plating_ah > 10 * warm.kpis["final_sei_loss_ah"]
    # ...and the cold cell loses clearly more capacity to plating than the warm one.
    assert cold_plating_ah > 2 * warm_plating_ah
    assert cold.kpis["final_soh"] < warm.kpis["final_soh"]


def test_lifetime_projection_scaling_and_eol():
    cold = bs.run_simulation(
        bs.SimConfig(profile=bs.PROFILE_FAST_CHARGE, ambient_temp_c=-10, degradation=True)
    )
    proj = bs.project_lifetime(cold)
    assert proj["eol_years"] is not None
    assert proj["eol_years"] < 1.0  # arctic fast charge is not a viable operating mode
    assert np.all(np.diff(proj["soh"]) <= 1e-12)

    # SEI-only loss follows sqrt(t): 4 years must be exactly 2x the 1-year loss
    sei_loss = 1.0 - proj["soh_sei_only"]
    i1, i4 = 24, 96  # indices of 1 and 4 years on the 241-point (10 yr) grid
    assert sei_loss[i4] == pytest.approx(2.0 * sei_loss[i1], rel=0.01)
    # plating-only loss is linear in cycles: same doubling test
    plat_loss = 1.0 - proj["soh_plating_only"]
    assert plat_loss[i4] == pytest.approx(4.0 * plat_loss[i1], rel=1e-9)

    with pytest.raises(ValueError):
        bs.project_lifetime(
            bs.run_simulation(bs.SimConfig(degradation=False))
        )


def test_lifetime_projection_mild_case_no_eol():
    mild = bs.run_simulation(
        bs.SimConfig(profile=bs.PROFILE_CONSTANT, degradation=True)
    )
    proj = bs.project_lifetime(mild)
    assert proj["eol_years"] is None  # discharge duty: no plating risk, tiny SEI
    assert proj["soh"][-1] > 0.99


def test_sweep_cooling_sizes_the_loop():
    desert = bs.SimConfig(profile=bs.PROFILE_PULSE, ambient_temp_c=45, cooling_h=30)
    sweep = bs.sweep_cooling(desert, h_values=[5, 15, 40, 100])
    # peak temperature decreases monotonically with h
    assert np.all(np.diff(sweep["peak_temps_c"]) < 0)
    assert sweep["exceeds_limit"]
    h_req = sweep["h_required"]
    assert h_req is not None and 5 < h_req < 100

    temperate = bs.SimConfig(profile=bs.PROFILE_PULSE, ambient_temp_c=25, cooling_h=30)
    mild = bs.sweep_cooling(temperate, h_values=[5, 15, 40, 100])
    assert not mild["exceeds_limit"]
    assert mild["h_required"] is None
    assert mild["peak_temps_c"].max() < bs.THERMAL_LIMIT_C
