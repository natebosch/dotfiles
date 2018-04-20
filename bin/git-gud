#!/bin/bash

updates="$(git pull 2>&1)"
exit_code=$?
deleted=($(echo "$updates" | sed -n "s/^ - \[deleted\] .* -> \(.*\)$/\1/p"))
echo "$updates"
echo "Removed: ${deleted[@]}"
for branch in "${deleted[@]/*\//}"; do
  if [ $(git rev-parse --verify "$branch" 2>/dev/null) ]; then
    git branch -D "$branch"
  fi
done
exit $exit_code
