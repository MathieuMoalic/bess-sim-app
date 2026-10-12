# Homeserver wiring for the bess-sim dashboard.
#
# The counterpart of this project's nixosModules.bess-sim-service: add this
# module to the homeserver flake (e.g. as nixosModules.bess-sim) and import
# it into the host configuration alongside
# inputs.bess-sim-app.nixosModules.bess-sim-service.
{
  config,
  pkgs,
  inputs,
  ...
}: let
  url = "bess.matmoa.eu";
  port = 8600;
in {
  # Lets you inspect /var/lib/bess-sim (venv, uv caches, logs).
  users.users.mat.extraGroups = ["bess-sim"];
  users.users.bess-sim.homeMode = "0750";

  services.bess-sim = {
    enable = true;

    # Streamlit stays on loopback; Caddy terminates TLS.
    bindAddress = "127.0.0.1";
    inherit port;

    # Auto-generates services.caddy.virtualHosts.${url} with
    #   reverse_proxy 127.0.0.1:<port>
    # websockets (live chart updates) work through Caddy out of the box.
    domain = url;

    # Behind Caddy the port never needs to face the network.
    openFirewall = false;

    # Optional — defaults to this flake's filtered app files
    # (app.py, bess_sim.py, pyproject.toml, uv.lock). Override only to
    # serve a different checkout:
    # source = inputs.bess-sim-app;
  };
}
