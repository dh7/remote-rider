#!/usr/bin/env bash
set -euo pipefail

git config --global user.email "sandbox@localhost" 2>/dev/null || true
git config --global user.name "Sandbox" 2>/dev/null || true
git config --global credential.helper store 2>/dev/null || true

WORKSPACE="/workspace"

if [[ -n "${REPO_URL:-}" ]] && [[ ! -d "$WORKSPACE/.git" ]]; then
    echo "Cloning ${REPO_URL}..."
    git clone "${REPO_URL}" "$WORKSPACE" || {
        echo "Clone failed — starting shell in empty workspace."
    }
fi

cd "$WORKSPACE" 2>/dev/null || true

if [[ -n "${BRANCH:-}" ]] && [[ -d "$WORKSPACE/.git" ]]; then
    git checkout -b "${BRANCH}" 2>/dev/null \
        || git checkout "${BRANCH}" 2>/dev/null \
        || echo "Branch '${BRANCH}' could not be checked out — staying on current branch."
fi

echo ""
echo "  workspace : $WORKSPACE"
echo "  branch    : $(git -C "$WORKSPACE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'none')"
echo "  claude    : $(claude --version 2>/dev/null || echo 'not found')"
echo ""

exec ttyd -W -a -p 7681 bash
