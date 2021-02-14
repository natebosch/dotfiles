{ config, pkgs, ... }:

let
  shared_packages = import ./shared_packages.nix;
in {
  programs.home-manager.enable = true;
  home.stateVersion = "21.05";

  home.username = builtins.getEnv "USER";
  home.homeDirectory = builtins.getEnv "HOME";


  home.packages = shared_packages.install;

  fonts.fontconfig.enable = true;

  programs.go.enable = true;

}
