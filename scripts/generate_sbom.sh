#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
output_dir="${1:-${repo_root}/artifacts/sbom}"

cyclonedx_py="${CYCLONEDX_PY:-}"
if [[ -z "${cyclonedx_py}" && -x "${repo_root}/AI_Shop-backend/AI_Shop-agent/.venv/bin/cyclonedx-py" ]]; then
  cyclonedx_py="${repo_root}/AI_Shop-backend/AI_Shop-agent/.venv/bin/cyclonedx-py"
fi
if [[ -z "${cyclonedx_py}" ]]; then
  cyclonedx_py="$(command -v cyclonedx-py || true)"
fi
if [[ -z "${cyclonedx_py}" ]]; then
  echo "cyclonedx-py is required; install cyclonedx-bom==7.3.1 or set CYCLONEDX_PY" >&2
  exit 127
fi

mkdir -p "${output_dir}"
output_dir="$(cd -- "${output_dir}" && pwd)"

(
  cd "${repo_root}/AI_Shop-backend"
  mvn --batch-mode --no-transfer-progress \
    org.cyclonedx:cyclonedx-maven-plugin:2.9.1:makeAggregateBom \
    -DskipTests \
    -DoutputFormat=json \
    -DoutputName=aishop-java \
    -DoutputDirectory="${output_dir}"
)

(
  cd "${repo_root}/AI_Shop-backend/AI_Shop-agent"
  "${cyclonedx_py}" requirements requirements.lock \
    --pyproject pyproject.toml \
    --output-reproducible \
    --output-format JSON \
    --output-file "${output_dir}/aishop-python.json" \
    --validate
)

for app in AI_Shop-admin AI_Shop-web; do
  (
    cd "${repo_root}/AI_Shop-front/${app}"
    npx --yes @cyclonedx/cyclonedx-npm@6.0.1 \
      --package-lock-only \
      --output-reproducible \
      --output-format JSON \
      --output-file "${output_dir}/${app}.json" \
      --validate
  )
done

python - "${output_dir}" <<'PY'
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
files = sorted(output.glob("*.json"))
expected = {"aishop-java.json", "aishop-python.json", "AI_Shop-admin.json", "AI_Shop-web.json"}
missing = expected - {path.name for path in files}
if missing:
    raise SystemExit(f"SBOM files missing: {sorted(missing)}")
for path in files:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("bomFormat") != "CycloneDX" or not payload.get("specVersion"):
        raise SystemExit(f"invalid CycloneDX document: {path}")
    print(f"{path.name}: {len(payload.get('components') or [])} components")
PY
