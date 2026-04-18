{
  description = "Home Manager configuration";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, home-manager, ... }:
    let
      commonModules = [
        ./nix-modules/core.nix
        ./nix-modules/cli.nix
        ./nix-modules/git.nix
        ./nix-modules/editor.nix
        ./nix-modules/tmux.nix
        ./nix-modules/dev.nix
      ];
    in
    {
      homeConfigurations = {
        "mac" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.aarch64-darwin;
          extraSpecialArgs = { 
            userEmail = "nbosch1@gmail.com";
          };
          modules = commonModules ++ [
            ./nix-modules/darwin.nix
            {
              home.username = "nate";
              home.homeDirectory = "/Users/nate";
            }
          ];
        };
        "linux" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          extraSpecialArgs = { 
            userEmail = "nbosch1@gmail.com";
          };
          modules = commonModules ++ [
            ./nix-modules/linux.nix
            {
              home.username = "nate";
              home.homeDirectory = "/home/nate";
            }
          ];
        };
        "glinux" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          extraSpecialArgs = { 
            userEmail = "nbosch@google.com";
          };
          modules = commonModules ++ [
            ./nix-modules/linux.nix
            {
              home.username = "nbosch";
              home.homeDirectory = "/usr/local/google/home/nbosch";
            }
          ];
        };
      };
    };
}
