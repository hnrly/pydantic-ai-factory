# The one image every arm of Software Factory Evals runs in.
#
# It holds no source code of its own: /workspace is empty until a run mounts its case's
# repository snapshot there, read-only. Build it with `python -m evals.design --build-image`,
# which passes the pinned Claude Code version and tags the image SANDBOX_IMAGE.

# A slim Debian base with Node, which Claude Code needs.
FROM node:22-bookworm-slim

# The Claude Code release, pinned in evals/design/version.py. There is no default on purpose.
ARG CLAUDE_CODE_VERSION

# ripgrep backs the search tools of Pydantic AI Harness's FileSystem.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN test -n "$CLAUDE_CODE_VERSION" \
    && npm install --global "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

RUN mkdir /workspace
WORKDIR /workspace

# Run as the base image's unprivileged user, not root.
USER node
