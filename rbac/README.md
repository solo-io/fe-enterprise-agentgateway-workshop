# Evaluator RBAC

`evaluator.yaml` creates a ServiceAccount (`agw-evaluator`), a ClusterRole, and a
ClusterRoleBinding. The role lists every verb, resource, and API group
explicitly, with no wildcards, and each rule has a comment naming the lab or
chart that needs it.

The role is broad on CRDs and ClusterRoles because a gateway install is
cluster-scoped, and Kubernetes refuses to let an identity grant more
permissions than it holds. The `escalate` and `bind` verbs are limited to the
exact ClusterRole and Role names the charts create.

## Setup

Run these two commands once. The first needs an admin identity.

```bash
kubectl apply -f rbac/evaluator.yaml
./rbac/mint-kubeconfig.sh
```

The script prints an `export KUBECONFIG=...` line. Run it, then run the
evaluation as the `agw-evaluator` identity. Options:

```bash
./rbac/mint-kubeconfig.sh --duration 24h --out /path/to/evaluator.kubeconfig
```

The default token lifetime is 8 hours and the default output is
`e2e/.work/evaluator.kubeconfig`.

## Running as your own identity

Running as your own admin identity also works. The evaluation report records
which identity ran it.
