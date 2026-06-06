#!/bin/bash
set -euo pipefail

ACCESS_TOKEN=${GITHUB_ACCESS_TOKEN:?GITHUB_ACCESS_TOKEN must be set}
RUNNER_NAME=${RUNNER_NAME:-$(hostname)}
RUNNER_LABELS=${RUNNER_LABELS:-self-hosted,linux,x64}
RUNNER_GROUP=${RUNNER_GROUP:-Default}

if [ -n "${GITHUB_ORG:-}" ]; then
  TARGET=${GITHUB_ORG}
  REG_TOKEN_URL="https://api.github.com/orgs/${GITHUB_ORG}/actions/runners/registration-token"
elif [ -n "${GITHUB_REPOSITORY:-}" ]; then
  TARGET=${GITHUB_REPOSITORY}
  REG_TOKEN_URL="https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/runners/registration-token"
else
  echo "ERROR: Either GITHUB_ORG or GITHUB_REPOSITORY must be set." >&2
  exit 1
fi

echo "Requesting registration token for ${TARGET}..."

REG_TOKEN=$(curl -sX POST \
  -H "Authorization: token ${ACCESS_TOKEN}" \
  -H "Accept: application/vnd.github.v3+json" \
  "${REG_TOKEN_URL}" \
  | jq .token --raw-output)

if [ "${REG_TOKEN}" == "null" ] || [ -z "${REG_TOKEN}" ]; then
  echo "ERROR: Failed to obtain registration token. Check your GITHUB_ACCESS_TOKEN and target (GITHUB_ORG or GITHUB_REPOSITORY)." >&2
  exit 1
fi

cd /home/docker/actions-runner

./config.sh \
  --url "https://github.com/${TARGET}" \
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
