---
name: detect-environment
description: Classify the target cluster and check CLIs, version, sizing, LoadBalancer, and permissions before an evaluation. Use at the start of a session and after the base install is removed.
---

# Detect the environment

Run from the repo root:

```bash
./e2e/lib/detect-env.sh
```

It prints one `key: value` line per field and writes `e2e/.work/env.json`. Read that file and tell the prospect what it found.

## Fields

- `class`: the cluster type (for example `eks`, `gke`, `aks`, `openshift`, `vind`, `kind`). It selects which labs apply; pass it as `--env <class>`.
- `server_version`: the Kubernetes API server version.
- `version_supported`: whether that version is in the range the labs support. `null` means the range could not be read, and the script prints a note; say the version was not checked.
- `nodes`: node count.
- `cpu_allocatable`, `memory_allocatable`: total allocatable CPU cores and memory in GiB.
- `meets_sizing`: whether those totals meet the POC sizing in `labs/installation/system-requirements.md`.
- `loadbalancer`: whether a Service of type LoadBalancer received an address. The labs need one. `skipped` means the probe was not run (the teardown check passes `--skip-lb-probe`).
- `existing_install`: an Enterprise Agentgateway install already on the cluster. Empty means a clean cluster.
- `identity`: the Kubernetes identity running the session. The report records it.
- `permissions`: `ok`, or the list of missing permissions.
- `clis`: versions of `kubectl`, `helm`, `jq`, `python3`, `bash` (the first on PATH, which must be 4 or later), and `pyyaml`, or `missing`.
- `namespaces`: the namespaces that existed when detection ran. `--uninstall-base` compares against this list to find leftovers, so run detection before the base install.
- `detected_at`: when detection ran.
- `signals`: the evidence behind `class`. Entries like `can-i-unchecked:<label>` mean a permission rule could not be checked yet because its CRD does not exist. That is expected on a fresh cluster.

## Exit codes

- 0: the session can run. Summarize the fields above in a few sentences.
- 1: detection could not talk to the cluster. Show the error and ask the prospect to check their kubeconfig and context.
- 2: the session must stop. Show the message. A missing CLI, missing PyYAML, a bash older than 4 first on PATH, an unsupported server version, or no LoadBalancer is the prospect's to fix. Missing permissions come with the instruction to run `kubectl apply -f rbac/evaluator.yaml`, which needs an admin identity; point at `rbac/README.md`. Do not continue until a re-run exits 0.
