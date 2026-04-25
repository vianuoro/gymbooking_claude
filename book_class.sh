#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  cat <<'EOF'
Usage: ./book_class.sh ACTIVITY_ID REQUEST_TOKEN UFPRT COOKIE_JAR [DATE]

Example:
  ./book_class.sh 147573892 CfDJ...Nfm cookies.txt 2026-04-27

You must first authenticate and save session cookies in COOKIE_JAR.
Then use the activityId and fresh tokens from the booking page.
EOF
  exit 1
fi

ACTIVITY_ID="$1"
REQUEST_TOKEN="$2"
UFPRT="$3"
COOKIE_JAR="$4"
DATE="${5:-$(date +%F)}"
BOOK_URL="https://nordicwellness.se/boka/boka-grupptraning/"
REFERER_URL="${BOOK_URL}?datum=${DATE}"

curl -v \
  --request POST "$BOOK_URL" \
  --header 'Origin: https://nordicwellness.se' \
  --header "Referer: $REFERER_URL" \
  --header 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7' \
  --header 'Accept-Language: en,sv;q=0.9,en-US;q=0.8' \
  --header 'DNT: 1' \
  --cookie "$COOKIE_JAR" \
  --form "activityId=$ACTIVITY_ID" \
  --form "__RequestVerificationToken=$REQUEST_TOKEN" \
  --form "ufprt=$UFPRT"
