{
  writeShellApplication,
  coreutils,
}:
writeShellApplication {
  name = "yank";
  runtimeInputs = [
    coreutils
  ];
  text = builtins.readFile ./yank.sh;
}
