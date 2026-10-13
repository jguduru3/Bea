#!/bin/bash
# Turns the end of a failed build's output into a GitHub error annotation.
# Job logs need a sign-in to read; annotations are visible to anyone who can see the run.
# Annotations are short, so download progress bars are dropped and only the last lines are kept.
#   annotate-failure.sh <log file> <title>
log="$1"; title="$2"
msg=$(tr -d '\r' < "$log" | grep -v -E '■|% of [0-9.]+ ?[KMG]iB|^\|' | tail -n 30 | tail -c 3500 \
  | sed -e 's/%/%25/g' | awk 'BEGIN{ORS="%0A"} {print}')
echo "::error title=${title}::${msg}"
