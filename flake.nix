{
  description = "BESS module thermal & degradation simulator (PyBaMM DFN + Streamlit)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = {self, nixpkgs, ...}: let
    system = "x86_64-linux";
    pkgs = import nixpkgs {inherit system;};
    lib = pkgs.lib;

    # Deployment source: just what the server needs — no .venv, tests or
    # dev files. Referencing this in the module copies it to the store.
    appSource = lib.cleanSourceWith {
      src = ./.;
      filter = path: type: let
        base = baseNameOf path;
      in
        (type == "directory"
          && !lib.elem base [".git" ".venv" ".direnv" ".pytest_cache" "__pycache__" "tests" ".streamlit"])
        || (lib.hasSuffix ".py" path)
        || lib.elem base ["pyproject.toml" "uv.lock" "README.md" "justfile"];
    };

    service = {
      lib,
      config,
      pkgs,
      ...
    }: let
      cfg = config.services.bess-sim;
      stateDir = "/var/lib/bess-sim";
    in {
      options.services.bess-sim = {
        enable = lib.mkEnableOption "BESS module thermal & degradation simulator (Streamlit dashboard)";

        source = lib.mkOption {
          type = lib.types.path;
          default = appSource;
          defaultText = lib.literalExpression "appSource (this flake's app files)";
          description = ''
            Source tree containing app.py, bess_sim.py, pyproject.toml and
            uv.lock. The pinned dependency set (pybamm 26.10) is materialized
            into a uv-managed venv on first start.
          '';
        };

        port = lib.mkOption {
          type = lib.types.port;
          default = 8600;
          description = "Port to serve the dashboard on.";
        };

        bindAddress = lib.mkOption {
          type = lib.types.str;
          default = "127.0.0.1";
          description = "Address to bind Streamlit to (keep loopback when behind Caddy).";
        };

        domain = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
          example = "bess.example.com";
          description = ''
            Domain served by Caddy via reverse proxy. When set, a
            services.caddy.virtualHosts entry is configured for this host.
            websockets (Streamlit live updates) are handled automatically.
          '';
        };

        openFirewall = lib.mkOption {
          type = lib.types.bool;
          default = false;
          description = "Open the firewall for the dashboard port (not needed behind Caddy).";
        };
      };

      config = lib.mkIf cfg.enable {
        users.users.bess-sim = {
          isSystemUser = true;
          group = "bess-sim";
          home = stateDir;
        };
        users.groups.bess-sim = {};

        systemd.services.bess-sim = {
          description = "BESS module thermal & degradation simulator (Streamlit)";
          # uv resolves/installs the pinned dependency set on first start,
          # which needs the network (pybamm is not in nixpkgs).
          wants = ["network-online.target"];
          after = ["network-online.target"];
          wantedBy = ["multi-user.target"];

          environment = {
            HOME = stateDir;
            XDG_CACHE_HOME = "${stateDir}/.cache"; # pybamm/streamlit caches
            UV_PROJECT_ENVIRONMENT = "${stateDir}/.venv";
            UV_CACHE_DIR = "${stateDir}/uv-cache";
            UV_PYTHON_INSTALL_DIR = "${stateDir}/uv-python";
            UV_LINK_MODE = "copy";
          };

          serviceConfig = {
            ExecStartPre = "${pkgs.uv}/bin/uv sync --frozen --no-dev --project ${cfg.source}";
            ExecStart = "${stateDir}/.venv/bin/streamlit run ${cfg.source}/app.py --server.address ${cfg.bindAddress} --server.port ${toString cfg.port} --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false";
            User = "bess-sim";
            Group = "bess-sim";
            StateDirectory = "bess-sim";
            WorkingDirectory = stateDir;
            Restart = "always";
            RestartSec = "5s";
            NoNewPrivileges = "yes";
            PrivateTmp = "yes";
            ProtectSystem = "strict";
            ProtectHome = "yes";
            ReadWritePaths = [stateDir];
            SocketBindAllow = ["tcp:${toString cfg.port}"];
            SocketBindDeny = "any";
          };
        };

        networking.firewall = lib.mkIf cfg.openFirewall {
          allowedTCPPorts = [cfg.port];
        };

        services.caddy = lib.mkIf (cfg.domain != null) {
          virtualHosts.${cfg.domain}.extraConfig = ''
            reverse_proxy ${cfg.bindAddress}:${toString cfg.port}
          '';
        };
      };
    };

    shell = pkgs.mkShell {
      buildInputs = [pkgs.uv];

      shellHook = ''
        if [ ! -d ".venv" ]; then
          uv venv .venv
        fi
        source .venv/bin/activate

      '';
    };
  in {
    devShells.${system}.default = shell;
    nixosModules.bess-sim-service = service;
  };
}
