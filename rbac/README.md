# Evaluator RBAC

`evaluator.yaml` creates a ServiceAccount (`agw-evaluator`), a ClusterRole, and a
ClusterRoleBinding. The role lists every verb, resource, and API group
explicitly, with no wildcards, and each rule has a comment naming the lab or
chart that needs it.

The role is broad on CRDs and ClusterRoles because a gateway install is
cluster-scoped, and Kubernetes refuses to let an identity grant more
permissions than it holds.

## Security model

The role guards against accidents, not against a hostile evaluator. It can
create and update ClusterRoles and ClusterRoleBindings, holds `escalate` and
`bind` on the chart roles, and can read Secrets cluster-wide, so it is
privilege-equivalent to cluster-admin. Use it on disposable or test clusters.
The `escalate` and `bind` rules name the chart release names and namespaces that
`--install-base` uses; if you change those, edit the role.

## Coverage

The role grants what `--install-base`, `--track llm`,
`--track mcp`, and `--uninstall-base` need. Labs outside those tracks (Kyverno,
inference, HPA, networking) return forbidden errors as the evaluator; run them
as an admin or add rules.

## Setup

Run these two commands once. The first needs an admin identity.

```bash
kubectl apply -f rbac/evaluator.yaml
./rbac/mint-kubeconfig.sh
```

The script embeds the cluster CA in the kubeconfig. It takes the CA from the
current context, or from the `kube-root-ca.crt` ConfigMap when the context skips
TLS verification (vind and some kind setups), and `--ca-file <path>` supplies it
directly. With the `kube-root-ca.crt` CA, the kubeconfig verifies the apiserver
as `kubernetes.default.svc`, the name pods use with that CA, because the address
such a context connects to is often missing from the apiserver certificate.
`--tls-server-name <name>` sets a different name. The script copies
`tls-server-name` and `proxy-url` from the current context. It checks that the
new kubeconfig reaches the cluster, then prints an `export KUBECONFIG=...` line;
when the check fails it deletes the kubeconfig and prints the error. Run the
export line, then run the evaluation as the `agw-evaluator` identity. Options:

```bash
./rbac/mint-kubeconfig.sh --duration 24h --out /path/to/evaluator.kubeconfig --ca-file /path/to/ca.crt
./rbac/mint-kubeconfig.sh --tls-server-name kubernetes
```

The default token lifetime is 8 hours and the default output is
`e2e/.work/evaluator.kubeconfig`.

## Running as your own identity

Running as your own admin identity also works. The evaluation report records
which identity ran it.
