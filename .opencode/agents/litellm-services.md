---
description: Minimal local agent for the litellm-services monorepo. Read AGENTS.md before acting.
mode: primary
steps: 8
permission:
  bash:
    "*": ask
    "git status*": allow
    "git diff*": allow
    "git log*": allow
    "kubectl get*": allow
    "kubectl describe*": allow
    "kubectl logs*": allow
    "kubectl diff*": allow
    "make *": allow
    "ls *": allow
    "cat *": allow
    "grep *": allow
  edit: ask
  webfetch: allow
---

Follow `AGENTS.md` for repo-specific rules. Before edits, inspect `git status`/`git diff`; never expose secrets or mutate the cluster with kubectl; validate YAML/builds and finish with a PR-based GitOps workflow.