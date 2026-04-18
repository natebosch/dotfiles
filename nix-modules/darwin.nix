{ pkgs, ... }:
{
  home.packages = with pkgs; [
    gnused
  ];
}
