"""UI wiring tests using Streamlit's AppTest harness — no browser needed.

These verify the conditional rendering logic in app.py: the degradation
toggle drives the SoH chart / plating callouts, and the thermal status pill
switches states.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

FAST_CHARGE = "EV Fast Charge (CC-CV)"
CONSTANT = "Constant High-Power Discharge"


def fresh_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH), default_timeout=180)


def all_markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_baseline_run_no_degradation():
    at = fresh_app().run()
    assert not at.exception
    md = all_markdown(at)
    assert "Peak cell temperature" in md
    assert "Within thermal envelope" in md
    # 4 KPI cards + 3 always-on section headers carry hover explanations
    assert md.count("info-tip") >= 7
    # SoH chart hidden; prompt to enable degradation models shown instead
    captions = " ".join(c.value for c in at.caption)
    assert "Enable the degradation models" in captions
    # layperson glossary is present
    assert "Plain-English glossary" in " ".join(e.label for e in at.expander)


def test_cold_fast_charge_shows_plating_warning():
    at = fresh_app().run()
    at.sidebar.selectbox[0].set_value(FAST_CHARGE)
    at.sidebar.slider[0].set_value(-10)  # ambient temperature
    at.sidebar.checkbox[0].check()  # degradation models
    at.run()
    assert not at.exception
    warnings = " ".join(w.value for w in at.warning)
    assert "Lithium plating dominated" in warnings
    assert "Li plating" in all_markdown(at)  # the red status pill
    md = all_markdown(at)
    assert "State of health" in md  # SoH chart section appears
    assert "info-tip" in md  # hover explanations are wired in


def test_discharge_cycle_plating_small_shows_info_branch():
    at = fresh_app().run()
    at.sidebar.selectbox[0].set_value(CONSTANT)
    at.sidebar.checkbox[0].check()
    at.run()
    assert not at.exception
    assert not at.warning  # negligible plating on a discharge-only profile
    infos = " ".join(i.value for i in at.info)
    assert "Capacity lost over this duty cycle" in infos


def test_hot_passive_case_flips_thermal_pill():
    at = fresh_app().run()
    at.sidebar.slider[0].set_value(45)  # ambient
    at.sidebar.slider[1].set_value(5)  # h — natural convection
    at.run()
    assert not at.exception
    assert "Thermal limit exceeded" in all_markdown(at)


def _click_preset(at: AppTest, text: str) -> None:
    button = next((b for b in at.sidebar.button if text in b.label), None)
    assert button is not None, f"preset button not found: {text}"
    button.click()
    at.run()
    assert not at.exception


def test_preset_buttons_apply_full_scenario():
    at = fresh_app().run()
    _click_preset(at, "Desert heat + passive")
    assert at.sidebar.selectbox[0].value == "Grid Peak Shaving (Pulse)"
    assert at.sidebar.slider[0].value == 45
    assert at.sidebar.slider[1].value == 5
    assert at.sidebar.checkbox[0].value is False
    assert "Thermal limit exceeded" in all_markdown(at)
    assert "Active" in " ".join(s.value for s in at.sidebar.success)

    _click_preset(at, "Desert + liquid")
    assert at.sidebar.slider[1].value == 100
    assert "Within thermal envelope" in all_markdown(at)


def test_preset_arctic_fast_charge_plating():
    at = fresh_app().run()
    _click_preset(at, "Arctic fast charge")
    assert at.sidebar.selectbox[0].value == FAST_CHARGE
    assert at.sidebar.slider[0].value == -10
    assert at.sidebar.checkbox[0].value is True
    warnings = " ".join(w.value for w in at.warning)
    assert "Lithium plating dominated" in warnings


def test_lifetime_projection_section_renders():
    at = fresh_app().run()
    _click_preset(at, "Arctic fast charge")
    md = all_markdown(at)
    assert "Lifetime projection" in md
    errors = " ".join(e.value for e in at.error)
    assert "EOL (80% SoH)" in errors  # arctic fast charge is not lifetime-viable

    # temperate discharge duty: no EOL within the horizon
    at.sidebar.selectbox[0].set_value(CONSTANT)
    at.run()
    assert not at.exception
    successes = " ".join(s.value for s in at.success)
    assert "No EOL within" in successes


def test_cooling_sweep_button():
    at = fresh_app().run()
    md = all_markdown(at)
    assert "Cooling-loop sizing" in md
    assert "show_sweep" not in at.session_state  # lazy: nothing computed yet

    sweep_btn = next(
        (b for b in at.button if "Compute sizing curve" in b.label), None
    )
    assert sweep_btn is not None
    sweep_btn.click()
    at.run()
    assert not at.exception
    assert at.session_state.get("show_sweep") is True
    # baseline at 25 °C holds the limit even under natural convection
    successes = " ".join(s.value for s in at.success)
    assert "Thermal limit is maintained across the whole range" in successes


def test_cooling_sweep_desert_sizes_the_loop():
    at = fresh_app().run()
    _click_preset(at, "Desert heat + passive")
    sweep_btn = next(
        (b for b in at.button if "Compute sizing curve" in b.label), None
    )
    sweep_btn.click()
    at.run()
    assert not at.exception
    successes = " ".join(s.value for s in at.success)
    assert "the loop must deliver h ≥" in successes
