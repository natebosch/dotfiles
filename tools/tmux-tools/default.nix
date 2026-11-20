{
  symlinkJoin,
  writeShellApplication,
  stdenv,
  shellcheck,
  bash,
  coreutils,
}:
let
  tmux-next-window = writeShellApplication {
    name = "tmux-next-window";
    runtimeInputs = [ ];
    text = builtins.readFile ./tmux-next-window.sh;
    checkPhase = ''
      runHook preCheck
      ${stdenv.shellDryRun} "$target"
      ${shellcheck}/bin/shellcheck "$target"
      PATH="${coreutils}/bin:$PATH" ${bash}/bin/bash ${./test-tmux-next-window.sh} "$target"
      runHook postCheck
    '';
  };
in
symlinkJoin {
  name = "tmux-tools";
  paths = [
    tmux-next-window
  ];
}
