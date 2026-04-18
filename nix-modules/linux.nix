{ pkgs, ... }:
{
  targets.genericLinux.enable = true;
  home.packages = with pkgs; [
    orpie
  ];
}
