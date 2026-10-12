# 🔋 BESS Module Thermal & Degradation Simulator

An interactive, physics-based web app for exploring thermal management and
degradation behaviour of battery energy storage system (BESS) modules —
built with [PyBaMM](https://pybamm.org) and [Streamlit](https://streamlit.io),
in a Voltpack / Voltrack-sized design context.

## What it models

| Layer | Implementation |
|---|---|
| Electrochemistry | Doyle-Fuller-Newman (DFN) pseudo-2D model, OKane2022 parameter set (LG M50, 5 Ah) |
| Thermal | Lumped single-node energy balance: `m·cₚ·Ṫ = Q_gen − hA·(T − T_amb)`, heat generation taken from the electrochemical solution |
| Duty cycles | Grid peak shaving (pulse), constant high-power discharge (1.5C / 2C), EV fast charge (CC-CV at 2C) |
| Degradation (optional) | Solvent-diffusion-limited SEI growth + irreversible lithium plating → per-mechanism capacity fade (SoH chart) |
| Lifetime projection | First-order extrapolation of one duty cycle: SEI ∝ √(calendar time), plating ∝ charge throughput; reports years-to-EOL (80% SoH) |
| Cooling sizing | On-demand sweep of h = 5–100 W/m²K → peak temperature curve with cooling-regime bands and the minimum h that holds the 60 °C limit |

Each duty cycle solves in ~1 s, so every sidebar change re-runs the full
physics model live. KPI cards flag the peak cell temperature
(green < 45 °C, amber 45–55 °C, red > 55 °C), the temperature rise above
ambient, minimum terminal voltage, and peak heat generation.

## Quick start

Requirements: Nix (flake-enabled). Everything else — Python 3.13, `uv`, the
`.venv` and all packages — is provisioned by the flake / `uv`.

```bash
# 1. Enter the dev shell (direnv users: it just works via `use flake`)
nix develop

# 2. Install dependencies into .venv
just setup        # or: uv sync --all-groups

# 3. Run the dashboard
just run          # or: uv run streamlit run app.py
```

Then open <http://localhost:8501>.

No Nix? Any Python ≥ 3.13 with `uv` works too:

```bash
uv sync --all-groups
uv run streamlit run app.py
```

## Tasks (`just`)

| Recipe | What it does |
|---|---|
| `just run` | Launch the Streamlit dashboard |
| `just test` | Run the physics test suite (pytest) |
| `just test-one NAME` | Run tests matching a name |
| `just setup` | (Re)create `.venv` and install dependencies |
| `just shell` | Enter the Nix dev shell interactively |
| `just add PKG` / `just add-dev PKG` | Add a runtime / dev dependency |
| `just update` | Upgrade locked dependencies |
| `just check` | Fast import/parse sanity check |
| `just clean` | Remove caches (keeps `.venv`) |

All recipes run through `nix develop -c`, so `uv` is always available.

## Deploy on a NixOS homeserver (behind Caddy)

The flake exposes `nixosModules.bess-sim-service` — a systemd service
(`services.bess-sim`) that runs the dashboard as a dedicated system user
with hardened unit settings, and optionally wires the Caddy reverse proxy.

pybamm is not in nixpkgs (it ships compiled solver wheels), so the service
materializes the **exact `uv.lock`-pinned environment** (pybamm 26.10) into
`/var/lib/bess-sim/.venv` via `uv` on first start — it needs the network
once, then restarts are offline-instant.

On the homeserver's flake:

```nix
{
  inputs.bess-sim-app.url = "git+file:///home/mat/projects/bess-sim-app";
  # or, after pushing: "github:MathieuMoalic/bess-sim-app";

  outputs = {nixpkgs, bess-sim-app, ...}: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        bess-sim-app.nixosModules.bess-sim-service
        # ready-made host wiring (URL, port, groups, home mode):
        # (import /home/mat/projects/bess-sim-app/homeserver.nix)
        # …or configure inline:
        {
          services.bess-sim = {
            enable = true;
            port = 8600;                    # loopback; Caddy terminates TLS
            domain = "bess.example.com";    # creates the Caddy vhost
          };
          services.caddy.enable = true;     # + your ACME email config
        }
        # ...
      ];
    };
  };
}
```

`homeserver.nix` in this repo is the full wiring module with every option
exercised (URL, port, `extraGroups`, `homeMode`, `domain`, comments on
`source`/`openFirewall`) — copy it into the homeserver flake as its
`nixosModules.bess-sim` and import it.

Options: `enable`, `port` (8600), `bindAddress` (127.0.0.1), `domain`
(null → no Caddy vhost), `openFirewall` (false), `source` (defaults to this
flake's app files, filtered to `*.py` + lockfile).

First start takes a couple of minutes (Python + wheels download); watch it
with `journalctl -u bess-sim -f`. Streamlit's websocket live-updates work
through Caddy's `reverse_proxy` out of the box.

## Repository layout

```text
bess-sim-app/
├── app.py                  # Streamlit UI (layout, controls, Plotly charts)
├── bess_sim.py             # Physics layer: DFN + lumped thermal + SEI (Streamlit-free)
├── tests/test_bess_sim.py  # Duty-cycle, thermal-sensitivity and degradation tests
├── justfile                # Task runner
├── flake.nix               # Nix dev shell (uv)
├── pyproject.toml          # Dependencies (managed by uv)
├── requirements.txt        # Mirror of runtime deps, for reference
└── uv.lock                 # Locked dependency set
```

## Demo script (2 minutes)

Use the 🎬 **Demo scenarios** buttons in the sidebar — each sets the full
parameter set in one click:

1. **🌱 Baseline** — grid peak shaving at 25 °C with forced air: ~9 °C rise,
   everything green.
2. **🏜️ Desert heat + passive cooling** — 45 °C ambient, natural convection
   only: the cell sails past the 60 °C limit and the status pill turns red →
   the case for liquid cooling. Then hit **🔍 Compute sizing curve**: the
   sweep returns the minimum h the loop must deliver to hold the limit at
   this ambient — the cooling-sizing answer in one number.
3. **💧 Desert + liquid cold plate** — same desert day with h = 100:
   temperature pins near ambient. The cooling-sizing punchline.
4. **⚡ Room-temp fast charge** — 2C CC-CV at 25 °C with degradation on:
   plating already outpaces SEI (~2% capacity in one charge).
5. **🧊 Arctic fast charge** — the money shot: same charge at −10 °C plates
   ~4× more lithium (8%+ of nameplate in a single cycle) — and the lifetime
   projection shows EOL (80% SoH) in a matter of **days**, vs. the room-
   temperature charge's months-to-years. The physics case for charge-rate
   derating and pre-heating.

Drag any slider afterwards to riff on a scenario live — every change
re-solves the full model in ~1–2 s.

## Testing

```bash
just test
```

The suite covers: input validation, all duty cycles solving within physical
bounds, the current sign convention, C-rate scaling, CC-CV completion,
cooling/ambient thermal sensitivity, monotone per-mechanism capacity fade,
the cold-fast-charge plating case (cold cell plates > 2× the lithium of a
warm one), lifetime-projection scaling laws (SEI √t, plating linear in
cycles) and EOL detection, the cooling sweep's monotonicity and h sizing,
and Streamlit AppTest coverage of the UI (presets, conditional charts,
status pills, sweep button).
