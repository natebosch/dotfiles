{
  description = "Dev tooling for dev tooling";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, home-manager, ... }:
    let
      kedgeOverlay = final: prev: {
        kedge = prev.buildGoModule {
          pname = "kedge";
          version = "0.1.0";
          src = ./kedge;
          vendorHash = "sha256-7K17JaXFsjf163g5PXCb5ng2gYdotnZ2IDKk8KFjNj0=";
          doCheck = false;
          nativeBuildInputs = [ prev.installShellFiles ];
          postInstall = ''
            export PATH=$out/bin:$PATH
            installShellCompletion --cmd kedge \
              --bash <($out/bin/kedge completion bash) \
              --zsh <($out/bin/kedge completion zsh) \
              --fish <($out/bin/kedge completion fish)
          '';
        };
      };
    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ kedgeOverlay ];
        };
      in
      {
        packages.default = pkgs.kedge;
        packages.kedge = pkgs.kedge;

        checks = {
          kedge-tests = pkgs.kedge.overrideAttrs (old: {
            pname = "kedge-tests";
            doCheck = true;
          });

          kedge-integration = pkgs.runCommand "kedge-integration-test" {
            nativeBuildInputs = [ pkgs.kedge ];
          } ''
            # Create a mock plugin
            mkdir -p $out/bin
            printf "#!/bin/sh\necho 'Mock plugin executed'\n" > $out/bin/kedge-mockplugin
            chmod +x $out/bin/kedge-mockplugin

            # Run kedge help with the mock plugin in PATH
            PATH=$out/bin:$PATH kedge help > help_output.txt

            # Verify the mock plugin is listed in the help output
            if ! grep -q "mockplugin" help_output.txt; then
              echo "Integration test failed: kedge-mockplugin not found in help output"
              cat help_output.txt
              exit 1
            fi

            # Test execution
            PATH=$out/bin:$PATH kedge mockplugin > exec_output.txt
            if ! grep -q "Mock plugin executed" exec_output.txt; then
               echo "Integration test failed: kedge-mockplugin did not execute correctly"
               cat exec_output.txt
               exit 1
            fi

            echo "Integration test passed." > $out/success
          '';
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.go
            pkgs.kedge
            pkgs.golangci-lint
          ];
        };
      }
    ) // {
      homeConfigurations = {
        "mac" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.aarch64-darwin;
          modules = [
            ./nixpkgs/home.nix
            {
              home.username = "nate";
              home.homeDirectory = "/Users/nate";
              nixpkgs.overlays = [ kedgeOverlay ];
            }
          ];
        };
        "linux" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          modules = [
            ./nixpkgs/home.nix
            {
              home.username = "nate";
              home.homeDirectory = "/home/nate";
              nixpkgs.overlays = [ kedgeOverlay ];
            }
          ];
        };
        "glinux" = home-manager.lib.homeManagerConfiguration {
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          modules = [
            ./nixpkgs/home.nix
            {
              home.username = "nbosch";
              home.homeDirectory = "/usr/local/google/home/nbosch";
              nixpkgs.overlays = [ kedgeOverlay ];
            }
          ];
        };
      };
    };
}
