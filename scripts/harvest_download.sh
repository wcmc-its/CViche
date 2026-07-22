#!/usr/bin/env bash
# Download + validate + content-hash-dedupe CV .docx URLs into a target dir.
#
# The "which URLs" step is manual/search-driven (see the runbook) — this script only
# handles the mechanical download side: fetch each URL, keep it only if it is a real
# Word doc (ZIP magic + word/document.xml), skip HTTP failures / non-docx / exact dupes,
# and name survivors <prefix>NNN.docx continuing from whatever is already in dest_dir.
#
# PII: harvested CVs are real people's documents — dest_dir MUST be gitignored. Never commit them.
# Portable to macOS /bin/bash 3.2 (no associative arrays; reads URLs on FD 3 so curl can't eat stdin).
#
# Usage:
#   scripts/harvest_download.sh <urls_file> <dest_dir> [name_prefix]
#     urls_file    one .docx URL per line
#     dest_dir     where validated CVs land (gitignored)
#     name_prefix  output filename prefix (default: web)
set -u

URLS="${1:?usage: harvest_download.sh <urls_file> <dest_dir> [name_prefix]}"
DEST="${2:?usage: harvest_download.sh <urls_file> <dest_dir> [name_prefix]}"
PREFIX="${3:-web}"
mkdir -p "$DEST"
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15'
TMP=$(mktemp); HASHES=$(mktemp)
trap 'rm -f "$TMP" "$HASHES"' EXIT

hash_of(){ md5 -q "$1" 2>/dev/null || md5sum "$1" | awk '{print $1}'; }

# seed dedup set + next index from anything already in dest
maxidx=0
for f in "$DEST/$PREFIX"*.docx; do
  [ -e "$f" ] || continue
  hash_of "$f" >> "$HASHES"
  n=$(basename "$f" | sed -E "s/^${PREFIX}0*([0-9]+)\.docx/\1/")
  case "$n" in ''|*[!0-9]*) : ;; *) [ "$n" -gt "$maxidx" ] && maxidx=$n ;; esac
done

idx=$maxidx; ok=0; dup=0; bad=0; fail=0; lines=0
while IFS= read -r url <&3; do
  lines=$((lines+1)); [ -z "$url" ] && continue
  code=$(curl -sL -A "$UA" --max-time 45 -w '%{http_code}' -o "$TMP" "$url" </dev/null 2>/dev/null)
  [ "$code" = "200" ] || { fail=$((fail+1)); continue; }
  { [ "$(head -c2 "$TMP")" = "PK" ] && unzip -l "$TMP" 2>/dev/null | grep -q word/document.xml; } || { bad=$((bad+1)); continue; }
  h=$(hash_of "$TMP")
  grep -qxF "$h" "$HASHES" && { dup=$((dup+1)); continue; }
  echo "$h" >> "$HASHES"; idx=$((idx+1))
  cp "$TMP" "$DEST/$(printf '%s%03d.docx' "$PREFIX" "$idx")"; ok=$((ok+1))
done 3< "$URLS"

total=$(ls "$DEST/$PREFIX"*.docx 2>/dev/null | wc -l | tr -d ' ')
echo "harvest: urls=$lines new=$ok dup=$dup notdocx=$bad httpfail=$fail  total=$total"
