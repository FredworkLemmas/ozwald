#!/usr/bin/env bash

# Entrypoint to print environment variables and directory listings in YAML.
set -euo pipefail

yaml_escape() {
  # Escape for YAML single-quoted scalars: replace ' with ''
  local s="$1"
  s=${s//"'"/"''"}
  printf %s "$s"
}

print_environment_yaml() {
  echo "environment:"
  # Use env -0 to safely split, but many base images lack -0. Fall back.
  # We assume standard K=V format without newlines in values.
  env | sort | while IFS='=' read -r k v; do
    # Quote value as YAML single-quoted string
    qv=$(yaml_escape "${v:-}")
    echo "  ${k}: '${qv}'"
  done
}

list_directory_files_yaml() {
  local dir="$1"
  echo "  - directory: ${dir}"
  # If directory doesn't exist or isn't a dir, output empty list
  if [[ ! -d "$dir" ]]; then
    echo "    files: []"
    return 0
  fi

  echo "    files:"
  # List regular files (not directories), recursively. Print filename
  # (basename) and size in bytes.
  local entry
  while IFS= read -r -d '' entry; do
    local base size
    base=$(basename -- "$entry")
    # Use stat portable options: try GNU, then BSD
    if size=$(stat -c %s -- "$entry" 2>/dev/null); then
      :
    else
      size=$(stat -f %z -- "$entry" 2>/dev/null || echo 0)
    fi
    base_q=$(yaml_escape "$base")
    echo "      - filename: '${base_q}'"
    echo "        size: ${size}"
  done < <(find "$dir" -type f -print0)
}

print_file_listings_yaml() {
  local paths=${FILE_LISTING_PATHS:-}
  if [[ -z "$paths" ]]; then
    echo "file_listings: []"
    return 0
  fi

  echo "file_listings:"
  local IFS=':'
  # shellcheck disable=SC2206
  local arr=($paths)
  local p
  for p in "${arr[@]}"; do
    # Skip empty components (e.g., leading/trailing or ::)
    [[ -z "$p" ]] && continue
    list_directory_files_yaml "$p"
  done
}

main() {
  print_environment_yaml
  echo ""
  print_file_listings_yaml
}

main "$@"

# do nothing, forever
while true; do
  sleep 30
done
