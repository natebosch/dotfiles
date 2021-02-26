if [ -z "$DART_VERSION" ]; then
  DART_PATH="$DOTDIR/dart-sdk"
elif [[ "$DART_VERSION" == "local" ]]; then
  DART_PATH="$DOTDIR/repos/dart-sdk/sdk/out/ReleaseX64/dart-sdk"
elif [[ "$DART_VERSION" == "nnbd" ]]; then
  DART_PATH="$DOTDIR/repos/dart-sdk/sdk/out/ReleaseX64NNBD/dart-sdk"
else
  DART_PATH="$DOTDIR/.dart-sdks/$DART_VERSION"
fi

path=(
  ${path[@]:#*dart-sdk*}
  $DOTDIR/.pub-cache/bin
  $DART_PATH/bin
)

unset DART_PATH

alias pbr='pub run build_runner'
