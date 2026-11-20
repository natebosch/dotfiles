{
  symlinkJoin,
  python3Packages,
  writeShellApplication,
  coreutils,
  findutils,
  gnused,
  jq,
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
in
symlinkJoin {
  name = "code-harness";
  paths = [
    code-harness-cli
    git-enhance
  ];
}
