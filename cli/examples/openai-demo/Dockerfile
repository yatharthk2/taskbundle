# syntax=docker/dockerfile:1
# Tier-1 (hand-written) env for the OpenAI-solver demo: a tiny Python bundle whose entire repo
# fits an LLM context window. It is COMMITTED (not auto-detected) because it COPYs the stdlib
# solver agent into the image, where it runs in the solve box as `python /opt/openai_agent.py`.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY repo /workspace/repo
WORKDIR /workspace/repo
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir pytest

# The OpenAI solver agent: stdlib only; reads OPENAI_API_KEY from the env at RUNTIME (never baked
# into a layer). Vendored copy of cli/solvers/openai_agent.py — Docker's build context is the
# bundle dir, so it can't COPY a file from outside it.
COPY openai_agent.py /opt/openai_agent.py
