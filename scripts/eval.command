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
openai_api_key_set=${OPENAI_API_KEY+x}
openai_api_key_value=${OPENAI_API_KEY-}
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi
if [ "$yu_model_set" = x ]; then export MODEL="$yu_model_value"; fi
if [ "$yu_base_url_set" = x ]; then export BASE_URL="$yu_base_url_value"; fi
if [ "$yu_api_key_set" = x ]; then export API_KEY="$yu_api_key_value"; fi
if [ "$openai_api_key_set" = x ]; then export OPENAI_API_KEY="$openai_api_key_value"; fi
unset yu_model_set yu_model_value yu_base_url_set yu_base_url_value yu_api_key_set yu_api_key_value
unset openai_api_key_set openai_api_key_value
exec .venv/bin/python -m evals "$@"
