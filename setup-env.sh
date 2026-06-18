#!/bin/bash
# Source this file to set up Anthropic API environment variables
# Usage: source setup-env.sh

export ANTHROPIC_BASE_URL=https://aiportal-api.aws.lanl.gov
export ANTHROPIC_AUTH_TOKEN="sk-8XzYsJ9RqEhF30VCr5h1Dg"
export ANTHROPIC_DEFAULT_SONNET_MODEL=anthropic.claude-sonnet-4-5-20250929-v1:0
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1

echo "✓ Anthropic environment variables set"
