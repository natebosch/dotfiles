{ config, pkgs, ... }:

let
  shared_packages = import ./shared_packages.nix;
in {
  system.stateVersion = 4;

  environment.systemPackages = [
    pkgs.gnused
    pkgs.go
  ] ++ shared_packages.install;

  fonts.enableFontDir = true;

}
