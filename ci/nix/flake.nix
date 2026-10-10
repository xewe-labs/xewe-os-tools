# flake.nix (DRAFT, proposal P3): one toolchain definition for the developer shell (`nix develop`)
# and the CI runner (`nix develop --command ...`). Not evaluated yet: no nix on the machine that
# wrote it. Move it to the repository root of xewe-os-tools when P3 is chosen, then run
# `nix flake lock` once and commit flake.lock (that file is what makes it reproducible).
#
# What it pins: python + the tools' python deps, arduino-cli (the exact release from src/env/pins.py,
# checked against arduino's published sha256), esptool, clang-format, ruff, shellcheck, actionlint.
# What it does not pin: the esp32 core (about 6 GB). It still comes from `./setup.sh` through
# arduino-cli into XEWE_HOME, as today, and CI keeps caching it under the same key.
{
  description = "XeWe OS tools: developer shell and CI toolchain";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ] (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # keep in step with ARDUINO_CLI_VERSION in src/env/pins.py; the hashes are the lines of
        # https://github.com/arduino/arduino-cli/releases/download/v1.5.1/1.5.1-checksums.txt
        arduinoCliVersion = "1.5.1";
        arduinoCliAsset = {
          "x86_64-linux" = { platform = "Linux_64bit"; sha256 = "28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b"; };
          "aarch64-linux" = { platform = "Linux_ARM64"; sha256 = "1e69e077479f300614d4551334e0a33f08ee40b04315d83b8e7e0e94f0d0ee62"; };
          "aarch64-darwin" = { platform = "macOS_ARM64"; sha256 = "cb952e8c1621c95ef5f1d17831c945e3d0ec5973f89c557a7ec8feb9c4f7d4c9"; };
          "x86_64-darwin" = { platform = "macOS_64bit"; sha256 = "c982e940027996bea9901050e95fae99c59c1dcfee54beedecaf28141e7bf2e7"; };
        }.${system};

        # the upstream static binary, not nixpkgs' arduino-cli (whose version follows nixpkgs, not pins.py)
        arduino-cli = pkgs.stdenvNoCC.mkDerivation {
          pname = "arduino-cli";
          version = arduinoCliVersion;
          src = pkgs.fetchurl {
            url = "https://github.com/arduino/arduino-cli/releases/download/v${arduinoCliVersion}/arduino-cli_${arduinoCliVersion}_${arduinoCliAsset.platform}.tar.gz";
            inherit (arduinoCliAsset) sha256;
          };
          sourceRoot = ".";
          dontConfigure = true;
          dontBuild = true;
          installPhase = "install -Dm755 arduino-cli $out/bin/arduino-cli";
        };

        python = pkgs.python312.withPackages (ps: [
          ps.pyserial
          ps.pytest
          ps.pip
          ps.mypy
          ps.pyflakes
          ps.types-pyserial
        ]);

        tools = [
          python
          arduino-cli
          pkgs.esptool
          pkgs.clang-tools # clang-format
          pkgs.ruff
          pkgs.git
          pkgs.shellcheck
          pkgs.actionlint
        ];
      in
      {
        packages.arduino-cli = arduino-cli;

        devShells.default = pkgs.mkShell {
          packages = tools;
          shellHook = ''
            # ./setup.sh uses this arduino-cli instead of downloading one into build-tools/bin
            export XEWE_ARDUINO_CLI=${arduino-cli}/bin/arduino-cli
            export XEWE_CLANG_FORMAT=${pkgs.clang-tools}/bin/clang-format
            # the esp32 core's own esptool stays the default; `xewe flash` can use this one instead:
            # export XEWE_ESPTOOL="${pkgs.esptool}/bin/esptool"
            export XEWE_HOME="''${XEWE_HOME:-$HOME/.xewe-os}"
          '';
        };

        # `nix flake check` on CI: the tools' own pytest suite inside the same toolchain
        checks.pytest = pkgs.runCommand "xewe-tools-pytest" { nativeBuildInputs = tools; } ''
          cp -R ${self} src-tree && chmod -R u+w src-tree && cd src-tree
          mkdir -p "$TMPDIR/pkg" && ln -s "$PWD/src" "$TMPDIR/pkg/xewe"
          PYTHONPATH="$TMPDIR/pkg" XEWE_NO_BOARD=1 HOME="$TMPDIR" python -m pytest -q -p no:cacheprovider -p xewe.testing.plugin
          touch $out
        '';
      });
}
