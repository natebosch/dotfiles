GOCODE="$DOTDIR/.gocode"
if [[ ! -d "$GOCODE" ]]; then; mkdir "$GOCODE"; fi
export GOPATH="$GOCODE"
path+=("$GOCODE/bin")
