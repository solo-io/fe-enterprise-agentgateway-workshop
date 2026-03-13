# CrewAI Agent with MCP Tools and OBO Auth

## Pre-requisites
This lab assumes that you have completed the setup in `001` and `002`.

## Lab Objectives
- Deploy Keycloak as the in-cluster identity provider
- Upgrade the controller to enable the agentgateway STS (port 7777) for OBO token exchange
- Secure an OpenAI route and two MCP routes with JWT policies requiring STS-signed OBO tokens
- Run a Streamlit UI where a user logs in, receives a delegated OBO token, and drives a CrewAI agent entirely through agentgateway

## Overview

This lab demonstrates the full identity delegation flow in a production-style scenario. A Streamlit UI logs in with Keycloak and exchanges the raw user JWT plus a Kubernetes service account (obo-agent) token at the agentgateway STS (port 7777). The STS issues a delegated OBO token (RFC 8693) where `sub` carries the user's identity and `act.sub` carries the agent's service account identity. That OBO token is the only credential the CrewAI agent ever uses: it is passed as both the OpenAI API key (agentgateway strips it and injects the real key from a backend secretRef) and as the `Authorization` header on every MCP tool call (DeepWiki and Solo.io Docs, both multiplexed through agentgateway). Every route — `/openai`, `/agw-copilot/mcp` — is protected by an `EnterpriseAgentgatewayPolicy` requiring tokens signed by the STS; raw Keycloak JWTs are rejected with HTTP 401. The Streamlit sidebar shows the decoded before (Keycloak JWT) and after (OBO token with `act` claim) side by side, and a **Probe gateway with both tokens** button lets you confirm the 401 vs 200 live.

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

## Step 3 — Create the obo-agent Service Account

Create a dedicated service account for the agent and extract its identity values — you'll need them to configure the `may_act` mapper in the next step:

```bash
kubectl create serviceaccount obo-agent -n agentgateway-system

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

## Step 4 — Add the `may_act` Mapper to Keycloak

Add a hardcoded-claim protocol mapper to `agw-client`. This causes Keycloak to embed a `may_act` claim in every access token, explicitly authorizing `obo-agent` to act on behalf of the user:

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

## Step 5 — Upgrade the Controller to Enable the STS

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

## Step 6 — Verify the STS is Running

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

## Step 7 — Create the OpenAI Secret and Apply All Routes

Create the Kubernetes secret that holds your OpenAI API key. Agentgateway injects it on the backend — the agent never sees the raw key:

```bash
export OPENAI_API_KEY=$OPENAI_API_KEY
kubectl create secret generic openai-secret -n agentgateway-system \
  --from-literal="Authorization=Bearer $OPENAI_API_KEY" \
  --dry-run=client -oyaml | kubectl apply -f -
```

Apply the OpenAI backend and HTTPRoute:

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai: {}
  policies:
    auth:
      secretRef:
        name: openai-secret
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: agentgateway-system
  labels:
    example: openai-route
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
        - name: openai-all-models
          group: agentgateway.dev
          kind: AgentgatewayBackend
      timeouts:
        request: "120s"
EOF
```

Apply the MCP backend and HTTPRoute (multiplexes DeepWiki and Solo.io Docs):

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: copilot-mcp-backend
  namespace: agentgateway-system
spec:
  mcp:
    targets:
    - name: deepwiki
      static:
        host: mcp.deepwiki.com
        port: 443
        protocol: StreamableHTTP
        policies:
          tls: {}
    - name: soloiodocs
      static:
        host: search.solo.io
        port: 443
        protocol: StreamableHTTP
        policies:
          tls: {}
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: copilot-mcp
  namespace: agentgateway-system
spec:
  parentRefs:
  - name: agentgateway-proxy
  rules:
    - matches:
      - path:
          type: PathPrefix
          value: /agw-copilot/mcp
      timeouts:
        request: 0s
      backendRefs:
      - name: copilot-mcp-backend
        group: agentgateway.dev
        kind: AgentgatewayBackend
EOF
```

Apply the STS JWKS backend and the JWT policies that protect both routes:

```bash
kubectl apply -f - <<EOF
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: sts-jwks
  namespace: agentgateway-system
spec:
  static:
    host: enterprise-agentgateway.agentgateway-system.svc.cluster.local
    port: 7777
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: openai-obo-jwt-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: openai
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
---
apiVersion: enterpriseagentgateway.solo.io/v1alpha1
kind: EnterpriseAgentgatewayPolicy
metadata:
  name: copilot-mcp-obo-jwt-policy
  namespace: agentgateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: copilot-mcp
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

---

## Step 8 — Get the Gateway IP

```bash
export GATEWAY_IP=$(kubectl get svc -n agentgateway-system \
  --selector=gateway.networking.k8s.io/gateway-name=agentgateway-proxy \
  -o jsonpath='{.items[*].status.loadBalancer.ingress[0].ip}{.items[*].status.loadBalancer.ingress[0].hostname}')

echo "GATEWAY_IP=${GATEWAY_IP}"
```

---

## Step 9 — Verify JWT Enforcement

Confirm the route rejects requests that carry no OBO token:

```bash
curl -i "$GATEWAY_IP:8080/openai" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello"}]}'
```

Expected Output:

```
HTTP/1.1 401 Unauthorized
...
authentication failure: no bearer token found
```

---

## Step 10 — Install and Launch the Agent UI

Create a virtual environment and install dependencies:

```bash
python3.12 -m venv lib/crewai/agentgateway-copilot-with-obo/.venv
lib/crewai/agentgateway-copilot-with-obo/.venv/bin/pip install --upgrade pip -q
lib/crewai/agentgateway-copilot-with-obo/.venv/bin/pip install -r lib/crewai/agentgateway-copilot-with-obo/requirements.txt
```

Port-forward Keycloak and the STS (redirected to `/dev/null` to keep the terminal clean):

```bash
pkill -f "port-forward.*keycloak.*8080" 2>/dev/null || true; sleep 1
kubectl port-forward -n keycloak svc/keycloak 8080:8080 &>/dev/null &
pkill -f "port-forward.*7777" 2>/dev/null || true; sleep 1
kubectl port-forward -n agentgateway-system svc/enterprise-agentgateway 7777:7777 &>/dev/null &
sleep 3
```

Generate a fresh actor token (k8s SA token for `obo-agent`):

```bash
export ACTOR_TOKEN=$(kubectl create token obo-agent -n agentgateway-system --duration=3600s)
```

Launch Streamlit:

```bash
GATEWAY_IP="$GATEWAY_IP" \
ACTOR_TOKEN="$ACTOR_TOKEN" \
lib/crewai/agentgateway-copilot-with-obo/.venv/bin/streamlit run lib/crewai/agentgateway-copilot-with-obo/app.py
```

Expected Output:

```
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://...
```

---

## Step 11 — Demo Walkthrough

1. Open **http://localhost:8501** in your browser.
2. Before logging in, enter a question in the main form and click **Ask Expert**. The UI shows:
   - `Log in with Keycloak first (sidebar →)`
   - `Agentgateway rejected the request: HTTP 401`
   - `authentication failure: no bearer token found`

   This confirms the JWT policy is active — unauthenticated requests are rejected at the gateway before reaching the backend.
3. In the sidebar, enter `testuser` / `testuser` and click **Log in**.
4. The sidebar displays two decoded tokens side by side:
   - **User JWT** — issued by Keycloak (`iss` = `http://keycloak.keycloak.svc.cluster.local:8080/realms/obo-realm`), no `act` claim.
   - **OBO token** — issued by the agentgateway STS (`iss` = `enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777`), `sub` = original Keycloak user UUID, `act.sub` = `system:serviceaccount:agentgateway-system:obo-agent`.
5. Click **Probe gateway with both tokens**:
   - **User JWT (Keycloak)** column → `HTTP 401` — the gateway rejects it because it is not signed by the STS.
   - **OBO token (STS)** column → `HTTP 200` — accepted.
6. In the main form, enter a question (default: `What is agentgateway?`) and click **Ask Expert**.
7. Watch the **Agent Activity** panel: the agent invokes `deepwiki_read_wiki_structure`, `deepwiki_ask_question`, and `soloiodocs_search` — all MCP tool calls route through agentgateway at `/agw-copilot/mcp`, authenticated with the OBO token.
8. The **Answer** section renders the final response with YAML examples, source citations, and confidence scores.

---

## Step 12 — Verify in Gateway Logs

Inspect the proxy access log to confirm the STS JWT policy is validating tokens on every request:

```bash
kubectl logs deploy/agentgateway-proxy -n agentgateway-system --tail 20
```

Look for `error=authentication failure` on rejected requests and `http.status=200` on accepted ones. Example:

```
request gateway=default/agentgateway-proxy listener=http route=default/openai src.addr=127.0.0.1:59068
http.method=POST http.host=localhost http.path=/openai http.version=HTTP/1.1 http.status=401
error=authentication failure: no bearer token found duration=0ms
```

---

## Cleanup

```bash
# Kill port-forwards
pkill -f "port-forward.*keycloak" 2>/dev/null || true
pkill -f "port-forward.*7777" 2>/dev/null || true

# Remove gateway resources
kubectl delete httproute -n agentgateway-system openai copilot-mcp
kubectl delete agentgatewaybackend -n agentgateway-system openai-all-models copilot-mcp-backend sts-jwks
kubectl delete enterpriseagentgatewaypolicy -n agentgateway-system openai-obo-jwt-policy copilot-mcp-obo-jwt-policy
kubectl delete secret -n agentgateway-system openai-secret
kubectl delete serviceaccount -n agentgateway-system obo-agent

# Remove Keycloak
kubectl delete namespace keycloak

# Remove the Python virtual environment
rm -rf lib/crewai/agentgateway-copilot-with-obo/.venv

# Restore controller to its original configuration (no tokenExchange)
helm upgrade -i -n agentgateway-system enterprise-agentgateway \
  oci://us-docker.pkg.dev/solo-public/enterprise-agentgateway/charts/enterprise-agentgateway \
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
