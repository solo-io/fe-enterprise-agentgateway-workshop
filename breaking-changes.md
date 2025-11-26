# Labs currently not working
- `006-configure-routing-to-aws-bedrock.md` - access to Bedrock failing with `backend authentication failed: the credential provider was not enabled `
  - https://github.com/solo-io/gloo-gateway/issues/1132
- `008-jwt-auth-with-rbac.md` - Remote JWKS fetch is failing with `jwks ConfigMap isn't available`
  - https://github.com/solo-io/gloo-gateway/issues/1133
- `013-advanced-guardrails-webhook.md` - seems like a bug here since they moved from specifying host to only supporting a backendRef
- `018-mcp.md` - fails RBAC when using `mcp.tool.name`, seems like this may have changed to `mcp.resource.name` however when testing with that it also still fails

### Breaking Changes: `Backend` → `AgentgatewayBackend`

- **Removed**
  - `spec.type: AI`

- **Replaced**
  - `spec.ai.llm` → `spec.ai.provider`

- **Provider configuration moved**
  - `ai.llm.openai.model` → `ai.provider.openai.model`

- **Changed LLM provider fields**
  - `ai.llm.host` → `ai.host`
  - `ai.llm.port` → `ai.port`
  - `ai.llm.path.full` → `ai.path`

- **Authentication schema updated**
  - `authToken.secretRef` → `policies.auth.secretRef`
  - `authToken.kind: Passthrough` → `policies.auth.passthrough: {}`

- **Provider blocks now require explicit initialization**
  - `openai:` → `openai: {}`

- **HTTPRoute backend reference updated**
  - `kind: Backend` → `kind: AgentgatewayBackend`

Example Backend before:
```
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-all-models
  namespace: gloo-system
spec:
  type: AI
  ai:
    llm:
      openai:
        #--- Uncomment to configure model override ---
        #model: ""
        authToken:
          kind: SecretRef
          secretRef:
            name: openai-secret
```

Example `AgentgatewayBackend` after:
```
apiVersion: gateway.kgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai-all-models
  namespace: gloo-system
spec:
  ai:
    provider:
      openai: {}
        #--- Uncomment to configure model override ---
        #model: ""
  policies:
    auth:
      secretRef:
        name: openai-secret
```

### Breaking Changes: `GlooTrafficPolicy` → `AgentgatewayEnterprisePolicy`

- **Resource kind updated**
  - `kind: GlooTrafficPolicy` → `kind: AgentgatewayEnterprisePolicy`

- **The following is now moved under spec.traffic**
  - `spec.glooJWT` → `traffic.jwtAuthentication`
  - `spec.rbac` → `traffic.authorization`
  - `spec.glooRateLimit` → `spec.traffic.entRateLimit`
  - `spec.entExtAuth` → `spec.traffic.entExtAuth`

- **The following is now moved under spec.backend**
  - Guardrails & moderation → `backend.ai.promptGuard`
  - Webhooks → `backend.ai.promptGuard.request[].webhook.backendRef`

API Key Auth Before:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: api-key-auth
  namespace: gloo-system
spec:
  targetRefs:
    - name: agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  entExtAuth:
    authConfigRef:
      name: apikey-auth
      namespace: gloo-system
```

API Key Auth After:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: api-key-auth
  namespace: gloo-system
spec:
  targetRefs:
    - name: agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    entExtAuth:
      authConfigRef:
        name: apikey-auth
        namespace: gloo-system
```

JWT Auth Before:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  glooJWT:
    beforeExtAuth:
      providers:
        selfminted:
          issuer: https://dev.example.com
          jwks:
            local:
              key: |
                <key>
  rbac:
    policy:
      matchExpressions:
        - '(jwt.org == "internal") && (jwt.group == "engineering")'
```

JWT Auth After:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: agentgateway-jwt-auth
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: solo.io
          jwks:
            inline: |
                <key>
    authorization:
      policy:
        matchExpressions:
          - '(jwt.org == "solo.io") && (jwt.team == "team-id")'
```

Prompt Enrichment Before:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: openai-opt
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptEnrichment:
      prepend:
      - role: system
        content: "Return the response in JSON format"
```

Prompt enrichment after:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: openai-opt
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  backend:
    ai:
      prompt:
        prepend:
        - role: system
          content: "Return the response in JSON format"
```

Built-in Guardrails Before:
```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: openai-prompt-guard
  namespace: gloo-system
  labels:
    app: ai-gateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      request:
        customResponse:
          message: "Rejected due to inappropriate content"
        regex:
          action: REJECT
          matches:
          - pattern: "credit card"
            name: "CC"
```

Built-in Guardrails after:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: openai-prompt-guard
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  backend:
    ai:
      promptGuard:
        response:
        - regex:
            action: MASK
            builtins:
            - "CREDIT_CARD"
```

Webhook Guardrails before:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: openai-opt
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      request:
        customResponse:
          message: "Your request was rejected due to inappropriate content"
          statusCode: 403
        webhook:
          host:
            host: "ai-guardrail-webhook.gloo-system.svc.cluster.local"
            port: 8000
      response:
        webhook:
          host:
            host: "ai-guardrail-webhook.gloo-system.svc.cluster.local"
            port: 8000
```

Webhook Guardrails after (regression - not working)
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: openai-prompt-guard
  namespace: gloo-system
  labels:
    app: agentgateway
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  backend:
    ai:
      promptGuard:
        request:
          - webhook:
              backendRef:
                name: ai-guardrail-webhook
                namespace: gloo-system
                kind: Service
                port: 8000
        response:
          - webhook:
              backendRef:
                name: ai-guardrail-webhook
                namespace: gloo-system
                kind: Service
                port: 8000
```

Rate limit before:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: GlooTrafficPolicy
metadata:
  name: global-request-rate-limit
  namespace: gloo-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  glooRateLimit:
    global:
      rateLimitConfigRefs:
      - name: global-request-rate-limit
```

Rate limit after:
```yaml
apiVersion: gloo.solo.io/v1alpha1
kind: AgentgatewayEnterprisePolicy
metadata:
  name: global-request-rate-limit
  namespace: gloo-system
spec:
  targetRefs:
    - name: agentgateway
      group: gateway.networking.k8s.io
      kind: Gateway
  traffic:
    entRateLimit:
      global:
        rateLimitConfigRefs:
        - name: global-request-rate-limit
```


## Breaking Changes: PromptGuard / Moderation / Guardrails

- **Guardrail configuration moved**
  - `ai.promptGuard` → `backend.ai.promptGuard`

- **Request/response actions now expressed as lists**
  - `request:` → `request: [ … ]`
  - `response:` → `response: [ … ]`

- **Webhooks replaced**
  - `webhook.host.host` / `webhook.host.port` → `webhook.backendRef`

Before (simplified):
```
ai:
  promptGuard:
    request:
      regex: "credit card"
      customResponse:
        message: "blocked"
```

After:
```
backend:
  ai:
    promptGuard:
      request:
        - regex:
            - "credit card"
          response:
            message: "blocked"
```

Webhook before:
```
webhook:
  host:
    host: ai-guardrail-webhook.gloo-system.svc.cluster.local
    port: 8000
```

Webhook After:
```
webhook:
  backendRef:
    name: ai-guardrail-webhook
    namespace: gloo-system
    kind: Service
    port: 8000
```

## Breaking Changes: JWT Authentication
- moved into `traffic.jwtAuthentication`
- Inline JWKS and remote JWKS locations changed
- RBAC removed from legacy rbac block → now CEL-based under `traffic.authorization`

After:
```
traffic:
  jwtAuthentication:
    mode: Strict
    providers:
      - issuer: solo.io
        jwks:
          inline: |
            { "keys": [...] }

  authorization:
    policy:
      matchExpressions:
        - 'jwt.org == "engineering"'
```

## Breaking Changes: Rate Limiting
- Local rate limits now live under `traffic.rateLimit.local` and global under `traffic.entRateLimit.global`
- Removed old fields `glooRateLimit`