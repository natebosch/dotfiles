{
  symlinkJoin,
  python3Packages,
  writeShellApplication,
  writeTextFile,
  coreutils,
  findutils,
  gnused,
  jq,
  shellcheck,
}:
let
  code-harness-cli = python3Packages.buildPythonApplication {
    pname = "code-harness";
    version = "0.1.0";
    pyproject = true;

    src = ./.;

    build-system = [ python3Packages.setuptools ];

    doCheck = true;
    checkPhase = ''
      runHook preCheck
      python3 -m unittest discover -s tests -v
      runHook postCheck
    '';
  };

  git-enhance = writeShellApplication {
    name = "git-enhance";
    runtimeInputs = [
      coreutils
      findutils
      gnused
      jq
    ];
    text = builtins.readFile ./git-enhance.sh;
  };

  bash-env = writeTextFile {
    name = "code-harness-bash-env";
    destination = "/share/code-harness/bash-env.sh";
    text = builtins.readFile ./bash-env.sh;
    checkPhase = ''
      ${shellcheck}/bin/shellcheck "$target"
    '';
  };
in
symlinkJoin {
  name = "code-harness";
  paths = [
    code-harness-cli
    git-enhance
    bash-env
  ];
}
