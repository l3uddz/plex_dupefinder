#!/bin/sh

# Run Plex DupeFinder with Datadog APM enabled (IAST disabled due to probe conflicts)
# Preserve build-provided metadata; fall back to the current checkout for local runs.
DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA:-$(git rev-parse HEAD 2>/dev/null)}
DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL:-$(git config --get remote.origin.url 2>/dev/null)}
export DD_GIT_COMMIT_SHA DD_GIT_REPOSITORY_URL

DD_TRACE_AGENT_URL=unix:///var/run/datadog/apm.socket DD_RUNTIME_METRICS_ENABLED=true DD_RUNTIME_METRICS_RUNTIME_ID_ENABLED=true DD_ENV=production DD_SERVICE=plex_dupefinder DD_VERSION=1.0.6 DD_TRACE_DEBUG=true DD_TRACE_LOG_LEVEL=ERROR DD_IAST_ENABLED=true ddtrace-run python3 plex_dupefinder.py
