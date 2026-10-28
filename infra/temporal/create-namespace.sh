#!/bin/sh
set -eu

namespaces="${TEMPORAL_NAMESPACES:-${DEFAULT_NAMESPACE:-default}}"
address="${TEMPORAL_ADDRESS:-temporal:7233}"
max_attempts="${TEMPORAL_HEALTH_CHECK_MAX_ATTEMPTS:-30}"
sleep_seconds="${TEMPORAL_HEALTH_CHECK_SLEEP_SECONDS:-5}"
server_host="${address%%:*}"
server_port="${address##*:}"

attempt=1
while ! nc -z -w 10 "$server_host" "$server_port"; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "Temporal port did not become available after $max_attempts attempts."
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep "$sleep_seconds"
done

attempt=1
while ! temporal operator cluster health --address "$address" >/dev/null 2>&1; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "Temporal did not become healthy after $max_attempts attempts."
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep "$sleep_seconds"
done

for namespace in $namespaces; do
  if temporal operator namespace describe -n "$namespace" --address "$address" >/dev/null 2>&1; then
    echo "Temporal namespace '$namespace' already exists."
    continue
  fi

  temporal operator namespace create -n "$namespace" --address "$address"
  echo "Temporal namespace '$namespace' created."
done
