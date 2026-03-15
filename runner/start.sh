#!/bin/bash
set -euo pipefail

REPOSITORY=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set (owner/repo)}
ACCESS_TOKEN=${GITHUB_ACCESS_TOKEN:?GITHUB_ACCESS_TOKEN must be set}
RUNNER_NAME=${RUNNER_NAME:-$(hostname)}
RUNNER_LABELS=${RUNNER_LABELS:-self-hosted,linux,x64}
RUNNER_GROUP=${RUNNER_GROUP:-Default}

echo "Requesting registration token for ${REPOSITORY}..."

REG_TOKEN=$(curl -sX POST \
  -H "Authorization: token ${ACCESS_TOKEN}" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/${REPOSITORY}/actions/runners/registration-token" \
  | jq .token --raw-output)

if [ "${REG_TOKEN}" == "null" ] || [ -z "${REG_TOKEN}" ]; then
  echo "ERROR: Failed to obtain registration token. Check your GITHUB_ACCESS_TOKEN and GITHUB_REPOSITORY."
  exit 1
fi

cd /home/docker/actions-runner

./config.sh \
  --url "https://github.com/${REPOSITORY}" \
  --token "${REG_TOKEN}" \
  --name "${RUNNER_NAME}" \
  --labels "${RUNNER_LABELS}" \
  --runnergroup "${RUNNER_GROUP}" \
  --unattended \
  --replace

cleanup() {
  echo "Removing runner..."
  ./config.sh remove --unattended --token "${REG_TOKEN}"
}

trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

./run.sh & wait $!
