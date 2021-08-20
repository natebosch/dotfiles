{ pkgs ? import <nixpkgs> { } }:
let
  nixgl = (import
    (pkgs.fetchFromGitHub {
      owner = "guibou";
      repo = "nixGL";
      rev = "8687d0ea4c08893b47f7c727e1ef8284bc54c142";
      sha256 = "1w7hlz24zwj8mf0i4f7vwxylwgglzflk4zb1zkk429z6s1h486jl";
    })
    { }).nixGLIntel;
  glWrap = deriv: name: pkgs.symlinkJoin {
    name = name;
    paths = [
      (pkgs.writeShellScriptBin name
        "${nixgl}/bin/nixGLIntel ${deriv}/bin/${name} \"$@\"")
      deriv
    ];
  };
in
pkgs.mkShell {
  nativeBuildInputs = [
    (glWrap pkgs.prusa-slicer "prusa-slicer")
    (glWrap pkgs.openscad "openscad")
  ];
}
