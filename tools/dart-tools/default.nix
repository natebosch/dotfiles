{
  symlinkJoin,
  writeShellApplication,
  coreutils,
  curl,
  jq,
  unzip,
}:
let
  install-dart = writeShellApplication {
    name = "install-dart";
    runtimeInputs = [
      coreutils
      curl
      jq
      unzip
    ];
    text = builtins.readFile ./install-dart.sh;
  };
in
symlinkJoin {
  name = "dart-tools";
  paths = [
    install-dart
  ];
}
