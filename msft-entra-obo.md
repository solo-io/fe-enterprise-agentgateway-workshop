# Microsoft Entra ID On-Behalf-Of (OBO) Token Exchange

## Pre-requisites

This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

### Azure / Entra requirements

You need two app registrations in Azure Active Directory:

| Registration | Purpose |
|---|---|
| **Middle-tier app** | Represents the gateway client; your user token will target this app's API |
| **Downstream API app** | Represents the backend API; the OBO-exchanged token grants access to this app |

Configure the middle-tier app to have the **delegated permission** for the downstream API scope, and grant admin consent. Create a **client secret** for the middle-tier app — you will need it in Step 4.

### Required tools

- `kubectl` and `helm`
- `az` CLI (or any MSAL-capable tool) to obtain a user access token
- `jq` for decoding JWTs in the verification steps

> **Note:** This lab modifies the controller's `tokenExchange` configuration. If you have completed the [OBO Token Exchange Fundamentals lab](obo-token-exchange-fundamentals.md), run the cleanup from that lab first, or simply proceed — the Helm upgrade in Step 2 will replace whatever tokenExchange config is currently set.

---

## Lab Objectives

- Upgrade the controller to enable Entra OBO token exchange (STS validators pointing to Entra JWKS)
- Create the Entra client secret in Kubernetes
- Deploy gateway parameters, JWKS backend, demo httpbin backend, and HTTPRoute on the existing `agentgateway-proxy` gateway
- Apply a JWT authentication policy that validates incoming user tokens against Entra
- Apply an Entra OBO policy that exchanges the user token for a downstream API token
- Test: call the gateway with a user token and verify the backend receives the exchanged token

---

## Background

**Entra On-Behalf-Of (OBO)** is Microsoft's implementation of RFC 8693 token exchange. When a user calls a middle-tier service (the gateway), the gateway exchanges the user's Entra token for a new Entra token scoped to a downstream API — without the user ever needing to know about or authenticate to that downstream API directly.

```
Client
  │
  │  (1) User token (aud=middle-tier API)
  ▼
Enterprise Agentgateway
  │
  │  (2) Validate user token against Entra JWKS
  │  (3) Forward request to backend
  ▼
STS (port 7777)
  │
  │  (4) OBO request to Entra /oauth2/token
  │      subject_token=<user token>
  │      requested_scope=<downstream scope>
  │      client_id=<middle-tier app>
  │      client_secret=<middle-tier secret>
  ▼
Entra ID
  │
  │  (5) Returns exchanged token (aud=downstream API)
  ▼
Backend
  │  (6) Receives request with Authorization: Bearer <exchanged token>
```

**Contrast with the [OBO Token Exchange Fundamentals lab](obo-token-exchange-fundamentals.md) (Keycloak OBO):** In that lab the STS issues its own signed token (the backend validates against the STS JWKS). In this lab, the STS calls the Entra `/oauth2/token` endpoint and forwards the Entra-issued exchanged token directly — so the backend receives a real Entra token, not an STS-issued one. This is configured via `spec.backend.tokenExchange.entra` in the policy (rather than the internal STS exchange path used by Keycloak OBO).

---

## Step 1 — Set Environment Variables

Export these variables before running any subsequent steps. Replace the placeholder values with your actual Azure app registration details.

```bash
# Azure / Entra IDs
export ENTRA_TENANT_ID="$ENTRA_TENANT_ID"                             # e.g. "11111111-2222-3333-4444-555555555555"
export ENTRA_MIDDLETIER_CLIENT_ID="$ENTRA_MIDDLETIER_CLIENT_ID"      # e.g. "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
export ENTRA_DOWNSTREAM_SCOPE="$ENTRA_DOWNSTREAM_SCOPE"  # e.g. "api://ffffffff-0000-1111-2222-333333333333/.default"
export ENTRA_OBO_CLIENT_SECRET="$ENTRA_OBO_CLIENT_SECRET"

# Controller version and license (set these to match your environment)
export ENTERPRISE_AGW_VERSION=v2.2.0
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY
```

| Variable | Description |
|---|---|
| `ENTRA_TENANT_ID` | Azure AD tenant ID (GUID) |
| `ENTRA_MIDDLETIER_CLIENT_ID` | App registration ID for the middle-tier / gateway client |
| `ENTRA_DOWNSTREAM_SCOPE` | Scope URI for the downstream API (typically `api://<app-id>/.default`) |
| `ENTRA_OBO_CLIENT_SECRET` | Client secret for the middle-tier app (used for OBO exchange) |
| `ENTERPRISE_AGW_VERSION` | Enterprise Agentgateway chart version |
| `SOLO_TRIAL_LICENSE_KEY` | Solo trial license key |

---

## Step 2 — Upgrade the Controller to Enable Entra OBO Token Exchange

Upgrade the controller Helm release with a `tokenExchange` block. The `subjectValidator` and `apiValidator` point to Entra's JWKS endpoint so the STS can validate the incoming user tokens. The `actorValidator` remains Kubernetes.

```bash
helm upgrade -i -n agentgateway-system enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
--create-namespace \
--version $ENTERPRISE_AGW_VERSION \
--set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
-f -<<EOF
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-config
    namespace: agentgateway-system
tokenExchange:
  enabled: true
  issuer: "enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777"
  tokenExpiration: 24h
  subjectValidator:
    validatorType: remote
    remoteConfig:
      url: "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/discovery/v2.0/keys"
  apiValidator:
    validatorType: remote
    remoteConfig:
      url: "https://login.microsoftonline.com/${ENTRA_TENANT_ID}/discovery/v2.0/keys"
  actorValidator:
    validatorType: k8s
  elicitation:
    secretName: ""
EOF
```

> **Note:** The STS token exchange path for Entra OBO is `/oauth2/token` — this is the path used by the `EnterpriseAgentgatewayParameters` STS URI (Step 5). Do not confuse this with the internal RFC 8693 path `/oauth2/token/exchange` used in Keycloak OBO (the [OBO Token Exchange Fundamentals lab](obo-token-exchange-fundamentals.md)).

---

## Step 3 — Verify the Controller Restarted

Confirm the controller pod restarted cleanly:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=enterprise-agentgateway
```

Expected Output:

```
NAME                                       READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-79d8b8b47-f6fx8   1/1     Running   0          30s
```

Confirm port 7777 is exposed on the controller service:

```bash
kubectl get svc -n agentgateway-system enterprise-agentgateway
```

Expected Output:

```
NAME                      TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)             AGE
enterprise-agentgateway   ClusterIP   10.96.120.45    <none>        9977/TCP,7777/TCP   2m
```

Confirm the token exchange server started in the controller logs:

```bash
kubectl logs -n agentgateway-system deploy/enterprise-agentgateway | grep token
```

Expected Output (look for these lines):

```
{"level":"info","msg":"KGW_AGENTGATEWAY_TOKEN_EXCHANGE_CONFIG is set, starting AGW server with provided config","component":"tokenexchange"}
{"level":"info","msg":"starting token exchange server on","component":"tokenexchange","address":"0.0.0.0:7777"}
```

---

## Step 4 — Create the Entra Client Secret

Store the middle-tier app's client secret as a Kubernetes Secret. The Entra OBO policy (Step 9) will reference this secret when calling Entra's token endpoint.

```bash
kubectl create secret generic entra-obo-client-secret \
  -n agentgateway-system \
  --from-literal=client_secret="$ENTRA_OBO_CLIENT_SECRET"
```

---

## Step 5 — Add STS Parameters to the Gateway Config

Patch the existing `agentgateway-config` to add the STS URI required for Entra OBO exchange:

```bash
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type=merge \
  -p '{"spec":{"env":[{"name":"STS_URI","value":"http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/oauth2/token"},{"name":"STS_AUTH_TOKEN","value":"./var/run/secrets/xds-tokens/xds-token"}]}}'
```

Verify the env vars were applied:

```bash
kubectl get enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system -o jsonpath='{.spec.env}' | jq .
```

Expected Output:

```json
[
  {"name": "STS_URI", "value": "http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/oauth2/token"},
  {"name": "STS_AUTH_TOKEN", "value": "./var/run/secrets/xds-tokens/xds-token"}
]
```

---

## Step 6 — Deploy the JWKS Backend

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: entra-jwks
  namespace: agentgateway-system
spec:
  static:
    host: login.microsoftonline.com
    port: 443
  policies:
    tls: {}
EOF
```

This creates an `AgentgatewayBackend` named `entra-jwks` that points to `login.microsoftonline.com:443` with TLS enabled. The JWT authentication policy (Step 8) uses this backend to fetch Entra's JWKS for token validation.

---

## Step 7 — Deploy the Demo Backend and Route

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: obo-demo-backend
  namespace: agentgateway-system
spec:
  static:
    host: httpbin.agentgateway-system.svc.cluster.local
    port: 8000
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: jwt-secure-obo
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: obo-demo-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: httpbin
  namespace: agentgateway-system
---
apiVersion: v1
kind: Service
metadata:
  name: httpbin
  namespace: agentgateway-system
  labels:
    app: httpbin
    service: httpbin
spec:
  ports:
    - name: http
      port: 8000
      targetPort: 8080
  selector:
    app: httpbin
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: httpbin
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: httpbin
      version: v1
  template:
    metadata:
      labels:
        app: httpbin
        version: v1
    spec:
      serviceAccountName: httpbin
      containers:
        - image: docker.io/mccutchen/go-httpbin:v2.15.0
          imagePullPolicy: IfNotPresent
          name: httpbin
          ports:
            - containerPort: 8080
EOF
```

This creates:

| Resource | Kind | Description |
|---|---|---|
| `obo-demo-backend` | AgentgatewayBackend | Points to the in-cluster httpbin service |
| `jwt-secure-obo` | HTTPRoute | Routes all traffic to `obo-demo-backend` via `agentgateway-proxy` |
| `httpbin` | Deployment + Service + SA | Echo server for verifying headers and exchanged token |

Verify pods are running:

```bash
kubectl get pods -n agentgateway-system -l app=httpbin
```

Expected Output:

```
NAME                       READY   STATUS    RESTARTS   AGE
httpbin-6c7b5d4b8f-xq7tz   1/1     Running   0          30s
```

---

## Step 8 — Apply the JWT Authentication Policy

Apply a policy targeting the `jwt-secure-obo` HTTPRoute that validates incoming user tokens against Entra's JWKS. The policy enforces `Strict` mode — requests without a valid Entra token are rejected at the gateway before reaching the backend.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: jwt-secure-obo-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: jwt-secure-obo
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: https://sts.windows.net/${ENTRA_TENANT_ID}/
          audiences:
            - "api://${ENTRA_MIDDLETIER_CLIENT_ID}"
          jwks:
            remote:
              jwksPath: /${ENTRA_TENANT_ID}/discovery/v2.0/keys
              backendRef:
                name: entra-jwks
                kind: AgentgatewayBackend
                group: agentgateway.dev
                port: 443
EOF
```

---

## Step 9 — Apply the Entra OBO Token Exchange Policy

Apply a policy targeting the `obo-demo-backend` backend. In `ExchangeOnly` mode the gateway calls Entra's OBO endpoint before forwarding, and replaces the `Authorization` header with the exchanged token. The backend receives an Entra token scoped to the downstream API, never the raw user token.

```bash
kubectl apply -f - <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: obo-demo-entra-obo
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: agentgateway.dev
      kind: AgentgatewayBackend
      name: obo-demo-backend
  backend:
    tokenExchange:
      mode: ExchangeOnly
      entra:
        tenantId: "${ENTRA_TENANT_ID}"
        clientId: "${ENTRA_MIDDLETIER_CLIENT_ID}"
        scope: "${ENTRA_DOWNSTREAM_SCOPE}"
        clientSecretRef:
          name: entra-obo-client-secret
          key: client_secret
EOF
```

---

## Step 10 — Get the Gateway Address

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

export GATEWAY_URL="http://${GATEWAY_IP}:8080"
echo "Gateway: $GATEWAY_URL"
```

---

## Step 11 — Obtain a User Token from Entra

Log in with the `az` CLI and request an access token scoped to the middle-tier API:

```bash
az login

export USER_TOKEN=$(az account get-access-token \
  --resource "api://${ENTRA_MIDDLETIER_CLIENT_ID}" \
  --query accessToken -o tsv)

echo "User token (first 40 chars): ${USER_TOKEN:0:40}..."
```

> **Alternative:** If you have an MSAL app or are using a service principal, obtain the token via the device-code flow or client-credentials flow targeting the middle-tier app's API scope.

Decode the token payload to verify the audience:

```bash
_seg=$(echo "$USER_TOKEN" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_seg} % 4 )) -ne 0 ]; do _seg="${_seg}="; done
echo "$_seg" | base64 -d 2>/dev/null | jq '{iss, aud, scp}'
```

Expected Output:

```json
{
  "iss": "https://sts.windows.net/<your-tenant-id>/",
  "aud": "api://<your-middle-tier-client-id>",
  "scp": "..."
}
```

The `aud` must match `api://${ENTRA_MIDDLETIER_CLIENT_ID}` — this is what the JWT auth policy in Step 8 validates against.

---

## Step 12 — Call the Gateway and Verify OBO Token Exchange

Send a request to `GET /headers`. httpbin echoes back all request headers, so you can see the `Authorization` header that the backend actually received — which will contain the OBO-exchanged Entra token, not the original user token.

```bash
curl -i -H "Authorization: Bearer $USER_TOKEN" $GATEWAY_URL/headers
```

Expected Output:

```
HTTP/1.1 200 OK
content-type: application/json
...
{
  "headers": {
    "Authorization": [
      "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI..."
    ],
    "Host": [
      "httpbin.agentgateway-system.svc.cluster.local"
    ],
    ...
  }
}
```

The `Authorization` value in the response body is the OBO-exchanged token. Decode it to confirm it is scoped to the downstream API:

```bash
EXCHANGED_TOKEN=$(curl -s -H "Authorization: Bearer $USER_TOKEN" $GATEWAY_URL/headers \
  | jq -r '.headers.Authorization[0]' | sed 's/Bearer //')

_seg=$(echo "$EXCHANGED_TOKEN" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_seg} % 4 )) -ne 0 ]; do _seg="${_seg}="; done
echo "$_seg" | base64 -d 2>/dev/null | jq '{iss, aud, scp}'
```

Expected Output:

```json
{
  "iss": "https://sts.windows.net/<your-tenant-id>/",
  "aud": "<your-downstream-app-id>",
  "scp": "..."
}
```

The `aud` is now the downstream API — not the middle-tier app. The OBO exchange succeeded.

**Troubleshooting:**

| Symptom | Likely Cause |
|---|---|
| `401 authentication failure: no bearer token found` | Token not passed in the `Authorization` header |
| `401 authentication failure: token is expired` | Token expired; re-run Step 11 to get a fresh token |
| `401 authentication failure: JWT issuer not recognized` | `ENTRA_TENANT_ID` mismatch; check `iss` in your token vs. the policy |
| `401 authentication failure: JWT audience mismatch` | `ENTRA_MIDDLETIER_CLIENT_ID` mismatch; check `aud` in your token vs. the policy |
| `502` or `503` from STS | Controller not reachable on port 7777; re-check Step 3 |
| Entra OBO error in controller logs | Check `kubectl logs -n agentgateway-system deploy/enterprise-agentgateway` for Entra API errors |

---

## Step 13 — Verify the Request is Rejected Without a Token

Confirm the JWT auth policy is enforced — requests without a token should be rejected at the gateway:

```bash
curl -i $GATEWAY_URL/headers
```

Expected Output:

```
HTTP/1.1 401 Unauthorized
...
authentication failure: no bearer token found
```

---

## Cleanup

```bash
# Delete policies
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system jwt-secure-obo-policy obo-demo-entra-obo

# Delete route and backends
kubectl delete httproute -n agentgateway-system jwt-secure-obo
kubectl delete agentgatewaybackend -n agentgateway-system obo-demo-backend entra-jwks

# Delete httpbin
kubectl delete deployment -n agentgateway-system httpbin
kubectl delete service -n agentgateway-system httpbin
kubectl delete serviceaccount -n agentgateway-system httpbin

# Remove STS env vars from gateway config
kubectl patch enterpriseagentgatewayparameters agentgateway-config \
  -n agentgateway-system \
  --type=merge \
  -p '{"spec":{"env":null}}'

# Delete secret
kubectl delete secret -n agentgateway-system entra-obo-client-secret

# Restore controller to its original configuration (no tokenExchange)
helm upgrade -i -n agentgateway-system enterprise-agentgateway oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
--create-namespace \
--version $ENTERPRISE_AGW_VERSION \
--set-string licensing.licenseKey=$SOLO_TRIAL_LICENSE_KEY \
-f -<<EOF
gatewayClassParametersRefs:
  enterprise-agentgateway:
    group: enterpriseagentgateway.solo.io
    kind: EnterpriseAgentgatewayParameters
    name: agentgateway-config
    namespace: agentgateway-system
EOF
```
