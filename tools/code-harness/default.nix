{ python3Packages }:
python3Packages.buildPythonApplication {
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
}
