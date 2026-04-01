# OBO Token Exchange with Keycloak

## Pre-requisites
This lab assumes that you have completed the setup in `001`. `002` is optional but recommended if you want to observe metrics and traces.

## Lab Objectives
- Deploy Keycloak in-cluster as the identity provider
- Upgrade the controller to enable the STS (port 7777) pointing to Keycloak JWKS
- Deploy a protected mock backend and configure JWT auth requiring STS-issued tokens
- **Part A — Impersonation:** Exchange a Keycloak user JWT for an STS-signed OBO token (no actor token required)
- **Part B — Delegation:** Add the `may_act` mapper in Keycloak, then exchange user JWT + k8s SA token for a delegated OBO token containing both `sub` and `act`

## Background

**On-Behalf-Of (OBO) token exchange** (RFC 8693) allows an agent to act on behalf of an end user when calling downstream services. Instead of passing the raw user token forward, the agent exchanges it at a trusted Security Token Service (STS) for a new token that preserves the user's identity — and optionally embeds the agent's identity too.

```
User --[user JWT]--> STS :7777 --[OBO token (sub=user)]--> Protected Route
                                                                  (impersonation)

User --[user JWT + k8s SA token]--> STS :7777 --[delegated token (sub=user, act=agent)]--> Protected Route
                                                                  (delegation)
```

Enterprise Agentgateway includes a built-in STS on port 7777 that:
1. Validates the user's JWT against a configured JWKS endpoint
2. Optionally validates the agent's Kubernetes service account token
3. Issues a short-lived OBO token embedding the user identity (and optionally the actor identity)

This lab uses **Keycloak** as the identity provider. Keycloak supports the `may_act` claim natively via a hardcoded-claim protocol mapper — no custom image or plugin required.

---

## Step 1 — Deploy Keycloak In-Cluster

Deploy Keycloak 26.5.2 (StatefulSet) backed by PostgreSQL 15 in the `keycloak` namespace:

```bash
kubectl create namespace keycloak 2>/dev/null || true
kubectl apply -n keycloak -f lib/keycloak/deploy.yaml
```

Wait for Keycloak to be ready (this can take 1–2 minutes):

```bash
kubectl wait pod -n keycloak -l app=keycloak --for=condition=Ready --timeout=300s
```

Expected Output:

```
pod/keycloak-0 condition met
```

---

## Step 2 — Configure Keycloak (Realm, Client, User)

Port-forward Keycloak to localhost:8080, then create the realm, client, and test user via the Admin API:

```bash
pkill -f "port-forward.*keycloak.*8080" 2>/dev/null || true
sleep 1
kubectl port-forward -n keycloak svc/keycloak 8080:8080 &
sleep 3

export KEYCLOAK_URL="http://localhost:8080"

ADMIN_TOKEN=$(curl -s -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -d "username=admin" -d "password=admin" -d "grant_type=password" -d "client_id=admin-cli" | jq -r '.access_token')

curl -s -X POST "${KEYCLOAK_URL}/admin/realms" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"realm":"obo-realm","enabled":true}'

curl -s -X POST "${KEYCLOAK_URL}/admin/realms/obo-realm/clients" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "agw-client",
    "enabled": true,
    "clientAuthenticatorType": "client-secret",
    "secret": "agw-client-secret",
    "directAccessGrantsEnabled": true,
    "serviceAccountsEnabled": false
  }'

curl -s -X POST "${KEYCLOAK_URL}/admin/realms/obo-realm/users" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "testuser@example.com",
    "emailVerified": true,
    "firstName": "Test",
    "lastName": "User",
    "enabled": true,
    "requiredActions": [],
    "credentials": [{"type": "password", "value": "testuser", "temporary": false}]
  }'

export KEYCLOAK_JWKS_URL="http://keycloak.keycloak.svc.cluster.local:8080/realms/obo-realm/protocol/openid-connect/certs"
```

> **Note:** `KEYCLOAK_JWKS_URL` uses the in-cluster DNS name. The STS runs inside the cluster and will use this URL directly — no port-forward is needed for JWKS validation.

---

## Step 3 — Upgrade the Controller to Enable the STS

Upgrade the controller helm release, adding the `tokenExchange` block pointing to Keycloak's JWKS endpoint:

```bash
export SOLO_TRIAL_LICENSE_KEY=$SOLO_TRIAL_LICENSE_KEY
export ENTERPRISE_AGW_VERSION=v2.2.0
```

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
      url: "${KEYCLOAK_JWKS_URL}"
  actorValidator:
    validatorType: k8s
  apiValidator:
    validatorType: k8s
EOF
```

Restart the data plane proxy pods so they pick up the new STS JWKS endpoint:

```bash
kubectl rollout restart deployment -n agentgateway-system -l gateway.networking.k8s.io/gateway-name=agentgateway-proxy
kubectl rollout status deployment -n agentgateway-system -l gateway.networking.k8s.io/gateway-name=agentgateway-proxy
```

---

## Step 4 — Verify the STS is Running

Check that the controller pod restarted cleanly:

```bash
kubectl get pods -n agentgateway-system -l app.kubernetes.io/name=enterprise-agentgateway
```

Expected Output:

```
NAME                                       READY   STATUS    RESTARTS   AGE
enterprise-agentgateway-79d8b8b47-f6fx8   1/1     Running   0          45s
```

Confirm that port 7777 is exposed on the controller service:

```bash
kubectl get svc -n agentgateway-system enterprise-agentgateway
```

Expected Output:

```
NAME                      TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)             AGE
enterprise-agentgateway   ClusterIP   10.96.120.45    <none>        9977/TCP,7777/TCP   2m
```

Confirm the STS started successfully in the controller logs:

```bash
kubectl logs -n agentgateway-system deploy/enterprise-agentgateway | grep token
```

Expected Output (look for these lines):

```
{"level":"info","msg":"KGW_AGENTGATEWAY_TOKEN_EXCHANGE_CONFIG is set, starting AGW server with provided config","component":"tokenexchange"}
{"level":"info","msg":"starting token exchange server on","component":"tokenexchange","address":"0.0.0.0:7777"}
```

---

## Step 5 — Deploy the Mock Backend

Deploy the vLLM Simulator and expose it as a Kubernetes service:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpt-4o
  namespace: agentgateway-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpt-4o
  template:
    metadata:
      labels:
        app: mock-gpt-4o
    spec:
      containers:
      - args:
        - --model
        - mock-gpt-4o
        - --port
        - "8000"
        - --max-loras
        - "2"
        - --lora-modules
        - '{"name": "food-review-1"}'
        image: ghcr.io/llm-d/llm-d-inference-sim:latest
        imagePullPolicy: IfNotPresent
        name: vllm-sim
        env:
          - name: POD_NAME
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.name
          - name: POD_NAMESPACE
            valueFrom:
              fieldRef:
                apiVersion: v1
                fieldPath: metadata.namespace
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpt-4o-svc
  namespace: agentgateway-system
  labels:
    app: mock-gpt-4o
spec:
  selector:
    app: mock-gpt-4o
  ports:
    - protocol: TCP
      port: 8000
      targetPort: 8000
      name: http
  type: ClusterIP
EOF
```

Create the HTTPRoute and AgentgatewayBackend:

```bash
kubectl apply -f - <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mock-llm
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: mock-llm-backend
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
---
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mock-llm-backend
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: "mock-gpt-4o"
      host: mock-gpt-4o-svc.agentgateway-system.svc.cluster.local
      port: 8000
      path: "/v1/chat/completions"
  policies:
    auth:
      passthrough: {}
EOF
```

Verify the route is accessible before adding auth:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock-gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 200 OK
...
{"id":"chatcmpl-...","choices":[{"message":{"role":"assistant","content":"..."}}],...}
```

---

## Step 6 — Configure JWT Auth Requiring STS-Issued Tokens

Create a backend pointing to the STS JWKS endpoint so the policy can fetch keys dynamically:

```bash
kubectl apply -f- <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: sts-jwks
  namespace: agentgateway-system
spec:
  static:
    host: enterprise-agentgateway.agentgateway-system.svc.cluster.local
    port: 7777
EOF
```

Apply an `EnterpriseAgentgatewayPolicy` targeting the `mock-llm` HTTPRoute that requires tokens issued by the STS:

```bash
kubectl apply -f- <<EOF
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: obo-jwt-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mock-llm
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777
          jwks:
            remote:
              backendRef:
                name: sts-jwks
                namespace: agentgateway-system
                kind: AgentgatewayBackend
                group: agentgateway.dev
              jwksPath: .well-known/jwks.json
EOF
```

Verify the route now rejects requests without a token:

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock-gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 401 Unauthorized
...
authentication failure: no bearer token found
```

---

## Part A — Impersonation

In the impersonation flow, the STS validates the user JWT and re-issues it as an OBO token signed by the STS key. No actor token is required. Downstream services receive a token they can trust (signed by the STS), while the user's `sub` is preserved.

---

## Step 7A — Get a User JWT from Keycloak

The Keycloak port-forward from Step 2 is still running. Fetch a user access token:

```bash
export USER_JWT=$(curl -s -X POST "${KEYCLOAK_URL}/realms/obo-realm/protocol/openid-connect/token" \
  -d "username=testuser" -d "password=testuser" -d "grant_type=password" \
  -d "client_id=agw-client" -d "client_secret=agw-client-secret" | jq -r '.access_token')

echo "User JWT (first 40 chars): ${USER_JWT:0:40}..."
```

Decode the payload to inspect the claims:

```bash
_seg=$(echo "$USER_JWT" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_seg} % 4 )) -ne 0 ]; do _seg="${_seg}="; done
echo "$_seg" | base64 -d 2>/dev/null | jq '{iss, sub, exp}'
```

Expected Output:

```json
{
  "iss": "http://keycloak.keycloak.svc.cluster.local:8080/realms/obo-realm",
  "sub": "<keycloak-user-uuid>",
  "exp": 1741910400
}
```

The token is signed by Keycloak's key. There is no `may_act` claim yet — this is a plain user JWT suitable for the impersonation flow.

---

## Step 8A — Exchange for an OBO Token (Impersonation)

Port-forward the STS to localhost:7777:

```bash
pkill -f "port-forward.*7777" 2>/dev/null || true
sleep 1
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway 7777:7777 &
sleep 2
```

Call the STS token exchange endpoint. No `actor_token` is needed for impersonation:

```bash
export STS_RESPONSE=$(curl -s -X POST "http://localhost:7777/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Authorization: Bearer ${USER_JWT}" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
  -d "subject_token=${USER_JWT}" \
  -d "subject_token_type=urn:ietf:params:oauth:token-type:jwt")

echo "$STS_RESPONSE" | jq '.'
```

Expected Output:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 86400,
  "issued_token_type": "urn:ietf:params:oauth:token-type:access_token"
}
```

Export the OBO token and decode it:

```bash
export OBO_JWT=$(echo "$STS_RESPONSE" | jq -r '.access_token')

_seg=$(echo "$OBO_JWT" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_seg} % 4 )) -ne 0 ]; do _seg="${_seg}="; done
echo "$_seg" | base64 -d 2>/dev/null | jq '{iss, sub, act}'
```

Expected Output:

```json
{
  "iss": "enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777",
  "sub": "<keycloak-user-uuid>",
  "act": null
}
```

The OBO token is now signed by the **STS key** (not Keycloak). The `sub` is the original user identity. There is no `act` claim — this is the impersonation flow.

---

## Step 9A — Call the Protected Route with the OBO Token

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OBO_JWT" \
  -d '{"model": "mock-gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 200 OK
content-type: application/json
...
{"id":"chatcmpl-...","choices":[{"index":0,"message":{"role":"assistant","content":"Hello! How can I help you today?"},"finish_reason":"stop"}],"model":"mock-gpt-4o",...}
```

Verify that the **raw Keycloak JWT is rejected** (it is not signed by the STS):

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $USER_JWT" \
  -d '{"model": "mock-gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 401 Unauthorized
...
authentication failure: token uses the unknown key "..."
```

The Keycloak JWT is signed by Keycloak's key, not the STS. Only tokens issued by the STS are accepted by the route.

---

## Part B — Delegation

In the delegation flow, the user JWT must contain a `may_act` claim that explicitly authorizes a specific actor (the agent's service account identity) to act on the user's behalf. The STS validates both the user JWT and the actor token, then issues a delegated OBO token containing both `sub` (user) and `act` (agent).

---

## Step 10B — Create the Agent Service Account and Get the Actor Token

Create a dedicated service account for the agent:

```bash
kubectl create serviceaccount obo-agent -n agentgateway-system
```

Generate a bounded service account token and decode it to extract the identity values needed for the `may_act` claim:

```bash
export ACTOR_TOKEN=$(kubectl create token obo-agent -n agentgateway-system --duration=3600s)

_pl=$(echo "$ACTOR_TOKEN" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_pl} % 4 )) -ne 0 ]; do _pl="${_pl}="; done
_pl=$(echo "$_pl" | base64 -d 2>/dev/null)

export MAY_ACT_SUB=$(echo "$_pl" | jq -r '.sub')
export MAY_ACT_ISS=$(echo "$_pl" | jq -r '.iss')

echo "MAY_ACT_SUB=${MAY_ACT_SUB}"
echo "MAY_ACT_ISS=${MAY_ACT_ISS}"
```

Expected Output:

```
MAY_ACT_SUB=system:serviceaccount:agentgateway-system:obo-agent
MAY_ACT_ISS=https://kubernetes.default.svc.cluster.local
```

---

## Step 11B — Add the `may_act` Mapper to Keycloak

Add a hardcoded-claim protocol mapper to the `agw-client` Keycloak client. This causes Keycloak to embed a `may_act` claim in every access token issued for this client, identifying the `obo-agent` service account as the authorized actor.

```bash
ADMIN_TOKEN=$(curl -s -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -d "username=admin" -d "password=admin" -d "grant_type=password" -d "client_id=admin-cli" | jq -r '.access_token')

CLIENT_UUID=$(curl -s -X GET "${KEYCLOAK_URL}/admin/realms/obo-realm/clients?clientId=agw-client" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r '.[0].id')

MAY_ACT_JSON=$(jq -nc --arg sub "$MAY_ACT_SUB" --arg iss "$MAY_ACT_ISS" '{sub: $sub, iss: $iss}')

MAPPER_JSON=$(jq -n \
  --arg claim_name "may_act" \
  --arg claim_value "$MAY_ACT_JSON" \
  '{
    name: "may-act",
    protocol: "openid-connect",
    protocolMapper: "oidc-hardcoded-claim-mapper",
    config: {
      "claim.name": $claim_name,
      "claim.value": $claim_value,
      "jsonType.label": "JSON",
      "access.token.claim": "true",
      "id.token.claim": "false"
    }
  }')

curl -s -X POST "${KEYCLOAK_URL}/admin/realms/obo-realm/clients/${CLIENT_UUID}/protocol-mappers/models" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$MAPPER_JSON"
```

---

## Step 12B — Get a Fresh User JWT (now includes `may_act`)

Fetch a new user token — it will now contain the `may_act` claim:

```bash
export USER_JWT=$(curl -s -X POST "${KEYCLOAK_URL}/realms/obo-realm/protocol/openid-connect/token" \
  -d "username=testuser" -d "password=testuser" -d "grant_type=password" \
  -d "client_id=agw-client" -d "client_secret=agw-client-secret" | jq -r '.access_token')
```

Verify the `may_act` claim is present:

```bash
_seg=$(echo "$USER_JWT" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_seg} % 4 )) -ne 0 ]; do _seg="${_seg}="; done
echo "$_seg" | base64 -d 2>/dev/null | jq '{sub, may_act}'
```

Expected Output:

```json
{
  "sub": "<keycloak-user-uuid>",
  "may_act": {
    "sub": "system:serviceaccount:agentgateway-system:obo-agent",
    "iss": "https://kubernetes.default.svc.cluster.local"
  }
}
```

The `may_act.sub` matches the `obo-agent` service account identity — Keycloak is now authorizing this specific actor to act on behalf of the user.

---

## Step 13B — Create the Agent Test Pod

Deploy a test pod that uses the `obo-agent` service account. Kubernetes automatically mounts the service account token at `/var/run/secrets/kubernetes.io/serviceaccount/token`:

```bash
kubectl apply -f- <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: obo-agent-test
  namespace: agentgateway-system
spec:
  serviceAccountName: obo-agent
  containers:
  - name: curl
    image: curlimages/curl:latest
    command: ["sleep", "3600"]
  restartPolicy: Never
EOF

kubectl wait pod/obo-agent-test -n agentgateway-system --for=condition=Ready --timeout=60s
```

---

## Step 14B — Perform the Delegation Token Exchange

From inside the agent pod, call the STS in-cluster. The pod's mounted service account token is the `actor_token`; the user JWT (with `may_act`) is the `subject_token`. No port-forward is needed — the pod can reach the STS directly via in-cluster DNS:

```bash
export DELEGATED_TOKEN=$(kubectl exec obo-agent-test -n agentgateway-system -- /bin/sh -c "
  K8S_SA_TOKEN=\$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
  curl -s -X POST http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -d 'grant_type=urn:ietf:params:oauth:grant-type:token-exchange' \
    -d 'subject_token=$USER_JWT' \
    -d 'subject_token_type=urn:ietf:params:oauth:token-type:jwt' \
    -d \"actor_token=\$K8S_SA_TOKEN\" \
    -d 'actor_token_type=urn:ietf:params:oauth:token-type:jwt'
" | jq -r '.access_token')

echo "Delegated token (first 40 chars): ${DELEGATED_TOKEN:0:40}..."
```

Decode the delegated token to verify both identities are present:

```bash
_seg=$(echo "$DELEGATED_TOKEN" | cut -d. -f2 | tr '_-' '/+')
while [ $(( ${#_seg} % 4 )) -ne 0 ]; do _seg="${_seg}="; done
echo "$_seg" | base64 -d 2>/dev/null | jq '{iss, sub, act}'
```

Expected Output:

```json
{
  "iss": "enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777",
  "sub": "<keycloak-user-uuid>",
  "act": {
    "sub": "system:serviceaccount:agentgateway-system:obo-agent"
  }
}
```

The delegated token embeds both the user identity (`sub`) and the agent identity (`act.sub`). Downstream services can use both for authorization and audit.

---

## Step 15B — Call the Protected Route with the Delegated Token

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $DELEGATED_TOKEN" \
  -d '{"model": "mock-gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 200 OK
content-type: application/json
...
{"id":"chatcmpl-...","choices":[{"index":0,"message":{"role":"assistant","content":"Hello! How can I help you today?"},"finish_reason":"stop"}],"model":"mock-gpt-4o",...}
```

Verify that the raw Keycloak JWT is still rejected:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $USER_JWT" \
  -d '{"model": "mock-gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 401 Unauthorized
...
authentication failure: token uses the unknown key "..."
```

Only tokens issued by the STS — whether impersonation tokens (Part A) or delegation tokens (Part B) — are accepted by the protected route.

---

## Cleanup

```bash
# Kill port-forwards
pkill -f "port-forward.*keycloak" 2>/dev/null || true
pkill -f "port-forward.*7777" 2>/dev/null || true

# Remove gateway resources
kubectl delete httproute -n agentgateway-system mock-llm
kubectl delete agentgatewaybackend -n agentgateway-system mock-llm-backend sts-jwks
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system obo-jwt-policy
kubectl delete serviceaccount -n agentgateway-system obo-agent
kubectl delete pod obo-agent-test -n agentgateway-system
kubectl delete deploy -n agentgateway-system mock-gpt-4o
kubectl delete svc -n agentgateway-system mock-gpt-4o-svc

# Remove Keycloak
kubectl delete namespace keycloak

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
