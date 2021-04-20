if [ -z "$DART_VERSION" ]; then
  DART_PATH="$HOME/dart-sdk"
elif [[ "$DART_VERSION" == "local" ]]; then
  DART_PATH="$HOME/repos/dart-sdk/sdk/out/ReleaseX64/dart-sdk"
elif [[ "$DART_VERSION" == "nnbd" ]]; then
  DART_PATH="$HOME/repos/dart-sdk/sdk/out/ReleaseX64NNBD/dart-sdk"
else
  DART_PATH="$HOME/.dart-sdks/$DART_VERSION"
fi

path=(
  ${path[@]:#*dart-sdk*}
  $HOME/.pub-cache/bin
  $DART_PATH/bin
)

unset DART_PATH

alias pbr='pub run build_runner'
