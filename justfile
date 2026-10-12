# BESS simulator — task runner.
# uv lives inside the Nix dev shell, so every recipe enters it with `nix develop -c`.

# List available recipes (default).
default:
    @just --list

# Enter an interactive Nix dev shell (venv is auto-created & activated).
shell:
    nix develop

# Create/refresh the .venv and install all dependencies.
setup:
    nix develop -c uv sync --all-groups

# Run the Streamlit dashboard (http://localhost:8501).
run:
    nix develop -c uv run streamlit run app.py

# Run the physics test suite.
test:
    nix develop -c uv run pytest -v

# Run a single test by name, e.g. `just test-one test_pulse_solves_and_stays_in_bounds`.
test-one name:
    nix develop -c uv run pytest -v -k "{{ name }}"

# Add a runtime dependency (updates pyproject.toml + uv.lock).
add pkg:
    nix develop -c uv add "{{ pkg }}"

# Add a dev-only dependency.
add-dev pkg:
    nix develop -c uv add --dev "{{ pkg }}"

# Upgrade all locked dependencies to their latest compatible versions.
update:
    nix develop -c uv lock --upgrade

# Sanity-check that the app module imports without executing the UI.
check:
    nix develop -c uv run python -c "import ast, sys; ast.parse(open('app.py').read()); import bess_sim; print('OK')"

# Remove caches and test artifacts (keeps .venv).
clean:
    rm -rf .pytest_cache tests/__pycache__ __pycache__ .streamlit/*.toml.gc
