# syntax=docker/dockerfile:1
# Tier-1 (hand-written) env for a real SWE-bench Pro instance: ansible/ansible.
#
# This is the INTENDED path for a remote-URL repo: auto-detect can't read the stack from
# a repo that isn't cloned yet (it inspects the bundle's *local* files), so a remote bundle
# supplies its own Dockerfile (tier 1) or install_cmd (tier 2). `init` passes REPO/COMMIT
# as build args, exactly as it does for any remote repo.
#
# Reproducibility: ansible-core's transitive deps resolve at build time and the base uses a
# floating tag, so this is reproducible within a machine but not byte-exact across machines
# (see DESIGN.md). For a byte-exact env, SWE-bench Pro publishes a prebuilt per-instance image
# (jefzda/sweap-images:<dockerhub_tag>) that could be used as the FROM instead.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ARG REPO
ARG COMMIT
# shallow-fetch just the target commit — ansible's full history is large
WORKDIR /workspace
RUN git init -q repo \
    && cd repo \
    && git remote add origin "$REPO" \
    && git fetch -q --depth 1 origin "$COMMIT" \
    && git checkout -q FETCH_HEAD
WORKDIR /workspace/repo

# ansible-core installs from wheels on slim (no compilers needed); pytest runs the buckets
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir pytest==8.3.4
