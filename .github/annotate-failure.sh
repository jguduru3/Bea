#!/bin/bash
# Turns the end of a failed build's output into a GitHub error annotation.
# Job logs need a sign-in to read; annotations are visible to anyone who can see the run.
#   annotate-failure.sh <log file> <title>
log="$1"; title="$2"
msg=$(tail -n 60 "$log" | tr -d '\r' | sed -e 's/%/%25/g' | awk 'BEGIN{ORS="%0A"} {print}')
echo "::error title=${title}::${msg}"
