# Admission Control with Kyverno

Agentgateway decides whether a *request* is allowed. Kyverno decides whether the *configuration* is allowed. In this lab you put four governance policies in front of the Kubernetes API server, so the API server rejects unsafe `EnterpriseAgentgatewayBackend` manifests before the controller translates them.

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.
- `helm` CLI

> **Note: Kyverno is not a Solo.io component.** It's a CNCF policy engine you install alongside Enterprise Agentgateway. These steps run against Kyverno **v1.18.2** (chart `3.8.2`).

## Lab Objectives
- Install Kyverno and grant it the RBAC it needs to see Agentgateway custom resources
- Require every backend to be an `EnterpriseAgentgatewayBackend` by denying the OSS `AgentgatewayBackend` kind
- Restrict LLM traffic to an approved provider list with a `ClusterPolicy`
- Constrain which hosts an MCP backend may reach, so no one can point an agent at an unvetted tool server
- Forbid inline plaintext credentials at all three paths the backend schema accepts them

---

## Overview

### Deploy-time and runtime checks

Enterprise Agentgateway enforces behavior on live traffic: authentication, authorization, rate limits, guardrails. Each of those governs traffic someone has already configured to flow. Kyverno checks one layer earlier, at admission, where the API server can still reject the configuration itself.

```
                     ┌──────────────────────────────────────────┐
   kubectl apply     │  Kubernetes API server                   │
   ───────────────▶  │                                          │
   (a manifest)      │    ┌──────────────────────────────────┐  │
                     │    │  Kyverno admission webhook       │  │   CHECK 1
                     │    │  "is this config allowed?"       │  │   deploy-time
                     │    └────────────┬─────────────────────┘  │
                     │         admit   │   deny ──▶ kubectl err │
                     └─────────────────┼────────────────────────┘
                                       ▼
                        EnterpriseAgentgatewayBackend admitted
                                       │
                             controller translates
                                       ▼
                     ┌──────────────────────────────────────────┐
   client request    │  agentgateway proxy                      │
   ───────────────▶  │    authz · rate limits · guardrails      │   CHECK 2
                     │    "is this request allowed?"            │   runtime
                     └──────────────────────────────────────────┘
```

By the time a request reaches the proxy, the backend it targets already exists; runtime policy can limit or reject the request, but it cannot change what the backend points at.

---

## Install Kyverno

```bash
helm repo add kyverno https://kyverno.github.io/kyverno/
helm repo update kyverno

helm upgrade -i kyverno kyverno/kyverno \
  -n kyverno --create-namespace \
  --version 3.8.2 \
  --wait --timeout 5m
```

Verify all four controllers are running:

```bash
kubectl get pods -n kyverno
```

Expected output:

```
NAME                                             READY   STATUS    RESTARTS   AGE
kyverno-admission-controller-f9cd97578-zrzst     1/1     Running   0          32s
kyverno-background-controller-787877f547-dlx4s   1/1     Running   0          32s
kyverno-cleanup-controller-57cd7d464d-x2fn9      1/1     Running   0          32s
kyverno-reports-controller-5fcbbf79f7-8cqwl      1/1     Running   0          32s
```

## Grant Kyverno Access to the Agentgateway CRDs

Kyverno ships with RBAC for built-in Kubernetes types only. Its background and reports controllers cannot list or watch custom resources until you grant access, and Kyverno picks up those grants through label-aggregated `ClusterRole`s.

Apply this **before** the policies:

```bash
kubectl apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kyverno:agentgateway-backends
  labels:
    rbac.kyverno.io/aggregate-to-background-controller: "true"
    rbac.kyverno.io/aggregate-to-reports-controller: "true"
rules:
  - apiGroups:
      - agentgateway.dev
      - enterpriseagentgateway.solo.io
    resources:
      - "*"
    verbs:
      - get
      - list
      - watch
EOF
```

**Note:** If you apply a policy before these grants, Kyverno's background scanner reports the custom resources as clean without evaluating them. To recover, apply the ClusterRole and then restart `kyverno-reports-controller`.

---

## Policy 1: Enterprise Backends Only

This `ClusterPolicy` denies admission of the OSS `AgentgatewayBackend` kind:

```bash
kubectl apply -f - <<'EOF'
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: agentgateway-enterprise-crds-only
  annotations:
    policies.kyverno.io/title: Enterprise Backend CRDs Only
    policies.kyverno.io/category: AI Governance
    policies.kyverno.io/description: >-
      Denies the OSS AgentgatewayBackend kind so every backend on the cluster is
      declared as an EnterpriseAgentgatewayBackend and one policy set governs it.
spec:
  background: true
  rules:
    - name: block-oss-backend-kind
      match:
        any:
          - resources:
              kinds:
                - agentgateway.dev/v1alpha1/AgentgatewayBackend
      validate:
        failureAction: Enforce
        message: >-
          AgentgatewayBackend (agentgateway.dev) is not allowed on this cluster.
          Declare backends as EnterpriseAgentgatewayBackend
          (enterpriseagentgateway.solo.io) instead.
        deny: {}
EOF
```

Confirm the policy compiled:

```bash
kubectl get clusterpolicy agentgateway-enterprise-crds-only
```

Expected output:

```
NAME                                ADMISSION   BACKGROUND   READY   AGE   MESSAGE
agentgateway-enterprise-crds-only   true        true         True    3s    Ready
```

Try configuring the OSS kind:

```bash
kubectl apply -f - <<'EOF'
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: oss-openai-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: gpt-5.6-luna
EOF
```

Expected output:

```
Error from server: error when creating "STDIN": admission webhook "validate.kyverno.svc-fail" denied the request:

resource AgentgatewayBackend/agentgateway-system/oss-openai-backend was blocked due to the following policies

agentgateway-enterprise-crds-only:
  block-oss-backend-kind: AgentgatewayBackend (agentgateway.dev) is not allowed on
    this cluster. Declare backends as EnterpriseAgentgatewayBackend (enterpriseagentgateway.solo.io)
    instead.
```

The same spec under the Enterprise kind goes through:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: ent-openai-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: gpt-5.6-luna
EOF
```

Expected output:

```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/ent-openai-backend created
```

---

## Policy 2: Approved LLM Providers Only

Routing to an unapproved provider sends prompts and completions to a vendor without a vetted contract or data-processing agreement.

This `ClusterPolicy` allows only `openai` and `bedrock` as LLM providers:

```bash
kubectl apply -f - <<'EOF'
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: agentgateway-restrict-providers
  annotations:
    policies.kyverno.io/title: Restrict LLM Providers
    policies.kyverno.io/category: AI Governance
    policies.kyverno.io/description: >-
      Routes LLM traffic only to organization-approved providers, so prompts and
      completions stay inside vetted vendor relationships and regions.
spec:
  background: true
  rules:
    - name: validate-provider
      match:
        any:
          - resources:
              kinds:
                - enterpriseagentgateway.solo.io/v1alpha1/EnterpriseAgentgatewayBackend
      preconditions:
        all:
          - key: "{{ request.object.spec.ai.provider || '' }}"
            operator: NotEquals
            value: ""
      validate:
        failureAction: Enforce
        message: >-
          Only approved LLM providers are allowed (openai, bedrock).
        deny:
          conditions:
            all:
              - key: "{{ keys(request.object.spec.ai.provider) }}"
                operator: AllNotIn
                value:
                  - openai
                  - bedrock
EOF
```

Confirm the policy is ready:

```bash
kubectl get clusterpolicy agentgateway-restrict-providers
```

Expected output:

```
NAME                              ADMISSION   BACKGROUND   READY   AGE   MESSAGE
agentgateway-restrict-providers   true        true         True    6s    Ready
```

Now try an unapproved provider:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: anthropic-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      anthropic:
        model: claude-3-5-sonnet-20241022
EOF
```

Expected output:

```
Error from server: error when creating "STDIN": admission webhook "validate.kyverno.svc-fail" denied the request:

resource EnterpriseAgentgatewayBackend/agentgateway-system/anthropic-backend was blocked due to the following policies

agentgateway-restrict-providers:
  validate-provider: Only approved LLM providers are allowed (openai, bedrock).
```

The API server never created the backend, so the controller never saw it. `bedrock` is approved, so it goes through:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: bedrock-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      bedrock:
        region: us-east-1
        model: anthropic.claude-3-sonnet-20240229-v1:0
EOF
```

Expected output:

```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/bedrock-backend created
```

---

## Policy 3: MCP Targets Must Point at Approved Hosts

The tool server behind an MCP backend receives whatever your agents send it (prompts, retrieved documents, tool arguments) and returns the tool definitions your agents act on. An unvetted server can leak the data and tamper with the tools.

This `ClusterPolicy` allowlists MCP static target hosts (`spec.mcp.targets[].static.host`):

```bash
kubectl apply -f - <<'EOF'
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: agentgateway-restrict-mcp-hosts
  annotations:
    policies.kyverno.io/title: Restrict MCP Target Hosts
    policies.kyverno.io/category: AI Governance
    policies.kyverno.io/description: >-
      Constrains host-based MCP static targets to an approved list, preventing
      agents from being wired to unvetted third-party tool servers.
spec:
  background: true
  rules:
    - name: validate-mcp-host
      match:
        any:
          - resources:
              kinds:
                - enterpriseagentgateway.solo.io/v1alpha1/EnterpriseAgentgatewayBackend
      preconditions:
        all:
          - key: "{{ request.object.spec.mcp || '' }}"
            operator: NotEquals
            value: ""
      validate:
        failureAction: Enforce
        message: >-
          MCP static targets may only point at approved hosts (search.solo.io,
          mcp.internal.example.com).
        foreach:
          - list: "request.object.spec.mcp.targets[]"
            preconditions:
              all:
                - key: "{{ element.static.host || '' }}"
                  operator: NotEquals
                  value: ""
            deny:
              conditions:
                all:
                  - key: "{{ element.static.host }}"
                    operator: AnyNotIn
                    value:
                      - search.solo.io
                      - mcp.internal.example.com
EOF
```

Try an unapproved tool server:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: shadow-mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
      - name: shadow-target
        static:
          host: evil-mcp.example.com
          port: 443
          protocol: StreamableHTTP
          policies:
            tls: {}
EOF
```

Expected output:

```
Error from server: error when creating "STDIN": admission webhook "validate.kyverno.svc-fail" denied the request:

resource EnterpriseAgentgatewayBackend/agentgateway-system/shadow-mcp-backend was blocked due to the following policies

agentgateway-restrict-mcp-hosts:
  validate-mcp-host: 'validation failure: MCP static targets may only point at approved
    hosts (search.solo.io, mcp.internal.example.com).'
```

Kyverno admits an approved host. This is the backend from the [Remote MCP lab](../mcp/remote-mcp.md):

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: soloio-docs-mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
      - name: soloio-docs-mcp-target
        static:
          host: search.solo.io
          port: 443
          protocol: StreamableHTTP
          policies:
            tls: {}
EOF
```

Expected output:

```
enterpriseagentgatewaybackend.enterpriseagentgateway.solo.io/soloio-docs-mcp-backend created
```

> **Tip: allowlist by suffix.** To approve a whole domain rather than named hosts, swap the `deny` block for a Kyverno wildcard check. `operator: AnyNotIn` compares exact strings, so use `pattern` with `host: "*.internal.example.com"` inside the `foreach` instead.

---

## Policy 4: No Plaintext Credentials in Manifests

Backend auth accepts a `secretRef` or a literal `key`. The `key` form embeds the token in the manifest, where it lands in Git history and in the terminal scrollback of whoever applied it, and the controller admits it without a warning.

Confirm both forms exist:

```bash
kubectl explain enterpriseagentgatewaybackends.spec.policies.auth | grep -E "^  (key|secretRef)"
```

Expected output:

```
  key	<string>
  secretRef	<Object>
```

The schema accepts an inline `key` at three independent paths:

| Path | Applies to |
|---|---|
| `spec.policies.auth.key` | AI backends (and any backend-level auth) |
| `spec.mcp.targets[].static.policies.auth.key` | OSS-protocol MCP targets (`StreamableHTTP`, `SSE`) |
| `spec.entMcp.targets[].static.policies.auth.key` | Enterprise MCP targets (OpenAPI-protocol) |

This `ClusterPolicy` forbids inline credentials at all three paths, one rule per path:

```bash
kubectl apply -f - <<'EOF'
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: agentgateway-no-plaintext-credentials
  annotations:
    policies.kyverno.io/title: Forbid Inline Plaintext Credentials
    policies.kyverno.io/category: AI Governance
    policies.kyverno.io/description: >-
      Requires auth to reference a Secret rather than embedding a literal token in
      the manifest, keeping provider and tool-server credentials out of Git.
spec:
  background: true
  rules:
    - name: no-inline-key-backend
      match:
        any:
          - resources:
              kinds:
                - enterpriseagentgateway.solo.io/v1alpha1/EnterpriseAgentgatewayBackend
      validate:
        failureAction: Enforce
        message: >-
          Inline plaintext credentials are forbidden at spec.policies.auth.key.
          Use spec.policies.auth.secretRef instead.
        deny:
          conditions:
            all:
              - key: "{{ request.object.spec.policies.auth.key || '' }}"
                operator: NotEquals
                value: ""
    - name: no-inline-key-mcp-target
      match:
        any:
          - resources:
              kinds:
                - enterpriseagentgateway.solo.io/v1alpha1/EnterpriseAgentgatewayBackend
      preconditions:
        all:
          - key: "{{ request.object.spec.mcp || '' }}"
            operator: NotEquals
            value: ""
      validate:
        failureAction: Enforce
        message: >-
          Inline plaintext credentials are forbidden on MCP static targets.
          Use static.policies.auth.secretRef instead.
        foreach:
          - list: "request.object.spec.mcp.targets[]"
            deny:
              conditions:
                all:
                  - key: "{{ element.static.policies.auth.key || '' }}"
                    operator: NotEquals
                    value: ""
    - name: no-inline-key-entmcp-target
      match:
        any:
          - resources:
              kinds:
                - enterpriseagentgateway.solo.io/v1alpha1/EnterpriseAgentgatewayBackend
      preconditions:
        all:
          - key: "{{ request.object.spec.entMcp || '' }}"
            operator: NotEquals
            value: ""
      validate:
        failureAction: Enforce
        message: >-
          Inline plaintext credentials are forbidden on entMcp static targets.
          Use static.policies.auth.secretRef instead.
        foreach:
          - list: "request.object.spec.entMcp.targets[]"
            deny:
              conditions:
                all:
                  - key: "{{ element.static.policies.auth.key || '' }}"
                    operator: NotEquals
                    value: ""
EOF
```

Try an inline token on an MCP target:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: inline-key-mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
      - name: leaky-target
        static:
          host: search.solo.io
          port: 443
          protocol: StreamableHTTP
          policies:
            auth:
              key: sk-mcp-plaintext-token
EOF
```

Expected output:

```
Error from server: error when creating "STDIN": admission webhook "validate.kyverno.svc-fail" denied the request:

resource EnterpriseAgentgatewayBackend/agentgateway-system/inline-key-mcp-backend was blocked due to the following policies

agentgateway-no-plaintext-credentials:
  no-inline-key-mcp-target: 'validation failure: Inline plaintext credentials are
    forbidden on MCP static targets. Use static.policies.auth.secretRef instead.'
```

Now make the same mistake one level up, on an AI backend; a different rule in the same policy catches it:

```bash
kubectl apply -f - <<'EOF'
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayBackend
metadata:
  name: inline-key-ai-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      key: sk-openai-plaintext-token
EOF
```

Expected output:

```
Error from server: error when creating "STDIN": admission webhook "validate.kyverno.svc-fail" denied the request:

resource EnterpriseAgentgatewayBackend/agentgateway-system/inline-key-ai-backend was blocked due to the following policies

agentgateway-no-plaintext-credentials:
  no-inline-key-backend: Inline plaintext credentials are forbidden at spec.policies.auth.key.
    Use spec.policies.auth.secretRef instead.
```

The two messages name different rules: `no-inline-key-mcp-target` and `no-inline-key-backend`. Kyverno reports the rule name alongside the policy, so you can tell which path tripped a multi-rule policy.

---

## Observability

Admission decisions never appear in gateway metrics or access logs, because no request reaches the proxy. Look for them in Kyverno's report resources and in the admission controller's logs.

### View Policy Reports

Kyverno writes a `PolicyReport` per namespace for namespaced resources and a `ClusterPolicyReport` for cluster-scoped ones:

```bash
kubectl get policyreport -A
```

### View Blocked Admission Attempts

The admission controller logs each denial under the message `blocking admission request`. Its structured fields carry the operation, the policy that fired, and the resource:

```bash
kubectl logs -n kyverno -l app.kubernetes.io/component=admission-controller -c kyverno --tail=-1 \
  | sed -E 's/\x1b\[[0-9;]*m//g' \
  | grep "blocking admission request" \
  | grep -oE 'operation=[^ ]+|policy=[^ ]+|resource=[^ ]+' | paste - - -
```

### Check Policy Health

A policy that fails to compile still shows up in `kubectl get clusterpolicy`. Check `READY` and `MESSAGE`:

```bash
kubectl get clusterpolicy
```

Expected output:

```
NAME                                     ADMISSION   BACKGROUND   READY   AGE   MESSAGE
agentgateway-enterprise-crds-only        true        true         True    5m    Ready
agentgateway-no-plaintext-credentials    true        true         True    5m    Ready
agentgateway-restrict-mcp-hosts          true        true         True    5m    Ready
agentgateway-restrict-providers          true        true         True    5m    Ready
```

For the Grafana and Prometheus stack, see `002`. Kyverno exposes its own metrics on port `8000` of the admission controller, which gives you policy-evaluation counters alongside gateway metrics.

---

## Cleanup

Run the cleanup in this order:

```bash
# 1. Policies, before the resources they govern; this also removes their reports
kubectl delete clusterpolicy \
  agentgateway-enterprise-crds-only \
  agentgateway-restrict-providers \
  agentgateway-restrict-mcp-hosts \
  agentgateway-no-plaintext-credentials --ignore-not-found

# 2. Test backends admitted during the lab
kubectl delete enterpriseagentgatewaybackend -n agentgateway-system \
  ent-openai-backend bedrock-backend soloio-docs-mcp-backend --ignore-not-found

# 3. The OSS backend from the Policy 1 denial test, in case it was applied
#    before the policy was Ready
kubectl delete agentgatewaybackend -n agentgateway-system \
  oss-openai-backend --ignore-not-found

# 4. RBAC grant
kubectl delete clusterrole kyverno:agentgateway-backends --ignore-not-found
```

Confirm the lab left nothing behind. Run this **before** uninstalling Kyverno, while its CRDs still exist:

```bash
kubectl get enterpriseagentgatewaybackend -A
kubectl get clusterpolicy
kubectl get policyreport -A
```

Expected output: `No resources found` for all three.

Now remove Kyverno itself:

```bash
helm uninstall kyverno -n kyverno
kubectl delete namespace kyverno --ignore-not-found
```
