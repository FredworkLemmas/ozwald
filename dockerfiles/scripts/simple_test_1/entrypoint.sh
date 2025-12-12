#!/usr/bin/env bash
set -euo pipefail

print_info() {
  local hn ips4 now
  hn=$(hostname)

  # Collect global-scope IPv4 addresses on non-loopback interfaces
  # Example ip output line: "2: eth0    inet 172.17.0.2/16 ..."
  if command -v ip >/dev/null 2>&1; then
    ips4=$(ip -o -4 addr show scope global \
      | awk '{print $4}' \
      | cut -d/ -f1 \
      | paste -sd ' ' -)
  else
    ips4="none"
  fi

  # Current time in UTC (RFC3339 Zulu format)
  now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  echo "hostname:  ${hn}"
  echo "ipv4:      ${ips4:-none}"
  echo "time_utc:  ${now}"
  echo "-----"
}

while true; do
  print_info
  sleep 30
done
