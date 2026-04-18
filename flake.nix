{
  description = "Home Manager configuration";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      home-manager,
      ...
    }:
    let
      inherit (nixpkgs) lib;

      hosts = {
        mac = {
          system = "aarch64-darwin";
          username = "nate";
          homeDirectory = "/Users/nate";
          userEmail = "nbosch1@gmail.com";
          platformModule = ./nix-modules/darwin.nix;
        };
        linux = {
          system = "x86_64-linux";
          username = "nate";
          homeDirectory = "/home/nate";
          userEmail = "nbosch1@gmail.com";
          platformModule = ./nix-modules/linux.nix;
        };
        glinux = {
          system = "x86_64-linux";
          username = "nbosch";
          homeDirectory = "/usr/local/google/home/nbosch";
          userEmail = "nbosch@google.com";
          platformModule = ./nix-modules/linux.nix;
        };
      };

      systems = lib.unique (lib.mapAttrsToList (_: host: host.system) hosts);
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      mkHome =
        {
          system,
          username,
          homeDirectory,
          userEmail,
          platformModule,
        }:
        home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.${system};
          extraSpecialArgs = { inherit userEmail; };
          modules = [
            ./nix-modules/core.nix
            ./nix-modules/cli.nix
            ./nix-modules/git.nix
            ./nix-modules/editor.nix
            ./nix-modules/tmux.nix
            ./nix-modules/dev.nix
            platformModule
            { home = { inherit username homeDirectory; }; }
          ];
        };
    in
    {
      homeConfigurations = lib.mapAttrs (_: mkHome) hosts;

      # `nix flake check` builds every host configuration for this system.
      checks = forAllSystems (
        pkgs:
        lib.mapAttrs (_: home: home.activationPackage) (
          lib.filterAttrs (
            name: _: hosts.${name}.system == pkgs.stdenv.hostPlatform.system
          ) self.homeConfigurations
        )
      );

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
