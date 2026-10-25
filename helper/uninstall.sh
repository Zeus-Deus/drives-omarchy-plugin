#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { printf '%s\n' 'Run explicitly: sudo bash helper/uninstall.sh'; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd -- "$source_dir"
exec /usr/bin/python3 -B -m helper.installation uninstall "$source_dir"
