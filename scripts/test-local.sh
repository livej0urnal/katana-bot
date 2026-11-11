#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ $# -gt 1 || ($# -eq 1 && $1 != "--with-ollama") ]]; then
    echo "Usage: $0 [--with-ollama]" >&2
    exit 2
fi
python=.venv/bin/python
test -x "$python" || python3.13 -m venv .venv
docker info --format '{{.ServerVersion}}' >/dev/null

# This Compose project keeps its database and cache separate from the running bot.
env_file=.env.qa
if [[ ! -e "$env_file" ]]; then
    (umask 077; "$python" -c 'import secrets; print("POSTGRES_PASSWORD=" + secrets.token_hex(24))' > "$env_file")
fi
password=$(sed -n 's/^POSTGRES_PASSWORD=\([0-9a-f]\{48\}\)$/\1/p' "$env_file")
if [[ ${#password} -ne 48 ]]; then
    echo "Expected a generated POSTGRES_PASSWORD in $env_file." >&2
    exit 1
fi

compose=(docker compose --project-name katana-qa --env-file "$env_file" --profile cache)
export POSTGRES_PASSWORD="$password"
"$python" -m pip install -r requirements-dashboard.txt -r requirements-redis.txt 'httpx>=0.27,<1'
"${compose[@]}" up -d --wait postgres redis
if [[ ${1:-} == "--with-ollama" ]]; then
    "${compose[@]}" --profile ai up -d --wait ollama
    "${compose[@]}" exec -T ollama ollama pull gemma3:4b
    export TEST_OLLAMA_URL=http://127.0.0.1:11434
fi

test_db="katana_test_$("$python" -c 'import secrets; print(secrets.token_hex(6))')"
"${compose[@]}" exec -T postgres createdb -U katana "$test_db"
cleanup() {
    "${compose[@]}" exec -T postgres dropdb -U katana --if-exists "$test_db"
}
trap cleanup EXIT
export DATABASE_URL="postgresql+psycopg://katana:$password@127.0.0.1:5433/$test_db"
export TEST_DATABASE_URL="$DATABASE_URL"
export TEST_REDIS_URL=redis://127.0.0.1:6380/0
"$python" -m alembic upgrade head
"$python" -m unittest discover -s tests -v
