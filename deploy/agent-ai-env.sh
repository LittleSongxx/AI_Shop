#!/usr/bin/env bash
# Import only AI settings from the Python Agent dotenv file.
#
# The Java Search process does not parse dotenv files itself. Keeping this
# allowlist small prevents Python-only settings (or secrets such as the
# internal token) from being copied into every Java process accidentally.

load_agent_ai_env() {
  local env_file=${1:?dotenv file path is required}
  [[ -r "$env_file" ]] || return 0

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*([A-Z][A-Z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    case "$key" in
      LLM_API_KEY|LLM_BASE_URL|LLM_MODEL|EMBEDDING_API_KEY|EMBEDDING_BASE_URL|EMBEDDING_PATH|EMBEDDING_MODEL|DASHSCOPE_API_KEY|VLM_ENABLED|VLM_API_KEY|VLM_BASE_URL|VLM_MODEL|VLM_IMAGE_MAX_TOKENS|VLM_MAX_TOKENS|VLM_CONNECT_TIMEOUT_SECONDS|VLM_TIMEOUT|VLM_TIMEOUT_SECONDS)
        ;;
      *)
        continue
        ;;
    esac

    value="${BASH_REMATCH[2]}"
    value="${value#${value%%[![:space:]]*}}"
    value="${value%${value##*[![:space:]]}}"
    case "$value" in
      \"*\") value="${value:1:${#value}-2}" ;;
      \'*\') value="${value:1:${#value}-2}" ;;
    esac
    [[ -n "$value" ]] || continue

    # An explicitly exported non-empty value wins over the dotenv fallback.
    if [[ -z "${!key:-}" ]]; then
      printf -v "$key" '%s' "$value"
      export "$key"
    fi
  done <"$env_file"
}
