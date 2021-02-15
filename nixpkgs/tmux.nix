with import <nixpkgs> {};
stdenv.mkDerivation rec {
  pname = "tmux";
  version = "0526d074d0170ad248b06187b64f4e44a0c05dcc";

  src = fetchFromGitHub {
    owner = "tmux";
    repo = "tmux";
    rev = version;
    sha256 = "1fa1m9v2fl09b1853n2fjnkf9nakwhkczdd8gfllq46d72sl5kfa";
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
