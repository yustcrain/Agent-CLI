#!/bin/bash
set -e
cd -- "$(dirname -- "$0")/.."
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import pocketflow, prompt_toolkit, rich' >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
fi
yu_model_set=${MODEL+x}
yu_model_value=${MODEL-}
yu_base_url_set=${BASE_URL+x}
yu_base_url_value=${BASE_URL-}
yu_api_key_set=${API_KEY+x}
yu_api_key_value=${API_KEY-}
yu_permission_mode_set=${PERMISSION_MODE+x}
yu_permission_mode_value=${PERMISSION_MODE-}
yu_provider_set=${PROVIDER+x}
yu_provider_value=${PROVIDER-}
yu_output_format_set=${OUTPUT_FORMAT+x}
yu_output_format_value=${OUTPUT_FORMAT-}
yu_auth_token_set=${AUTH_TOKEN+x}
yu_auth_token_value=${AUTH_TOKEN-}
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi
if [ "$yu_model_set" = x ]; then export MODEL="$yu_model_value"; fi
if [ "$yu_base_url_set" = x ]; then export BASE_URL="$yu_base_url_value"; fi
if [ "$yu_api_key_set" = x ]; then export API_KEY="$yu_api_key_value"; fi
if [ "$yu_permission_mode_set" = x ]; then export PERMISSION_MODE="$yu_permission_mode_value"; fi
if [ "$yu_provider_set" = x ]; then export PROVIDER="$yu_provider_value"; fi
if [ "$yu_output_format_set" = x ]; then export OUTPUT_FORMAT="$yu_output_format_value"; fi
if [ "$yu_auth_token_set" = x ]; then export AUTH_TOKEN="$yu_auth_token_value"; fi
unset yu_model_set yu_model_value yu_base_url_set yu_base_url_value yu_api_key_set yu_api_key_value
unset yu_permission_mode_set yu_permission_mode_value
unset yu_provider_set yu_provider_value yu_output_format_set yu_output_format_value
unset yu_auth_token_set yu_auth_token_value
if [ "$#" -eq 0 ] && [ -z "${MODEL:-}" ] \
    && [ ! -f workspace/.pi-python/config.json ] \
    && [ ! -f "${HOME}/.config/pi-cli/config.json" ]; then
    exec .venv/bin/python -m picli --workspace ./workspace --demo
fi
exec .venv/bin/python -m picli --workspace ./workspace "$@"
