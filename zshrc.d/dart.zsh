#path=$(path_add '/usr/lib/google-dartlang/bin' $path)
path+=(/usr/lib/google-dartlang/bin(N-/))

alias dartium='DART_FLAGS="--checked" /usr/lib/google-dartlang/bin/dartium --user-data-dir=~/.dartiumprofile'


intellijversion="139.1603.1"
alias intellij="XMODIFIERS=\"\" ~/bin/intellij-eap/bin/idea.sh"

alias df='cd ..; git diff master --name-only | grep .dart$ | xargs /google/data/ro/teams/dart/bin/dartfmt -w ; popd'
