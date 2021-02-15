with import <nixpkgs> {};
stdenv.mkDerivation rec {
  pname = "tmux";
  version = "bc2e0cf7ff51c2ab13c7dcc792d25e11ba7a3ef4";

  src = fetchFromGitHub {
    owner = "tmux";
    repo = "tmux";
    rev = version;
    sha256 = "0h4zxyfziypc8a45ckirj9sc1x2wwkm3xgda7py2d6676bmw0p7x";
  };
  nativeBuildInputs = [
    autoreconfHook
    pkg-config
    bison
  ];
  buildInputs = [
    libevent
    makeWrapper
    ncurses
  ];
  configureFlags = [
    "--sysconfdir=/etc"
    "--localstatedir=/var"
  ];
}
