# Labs currently not working
- `006-configure-routing-to-aws-bedrock.md` - access to Bedrock failing with `backend authentication failed: the credential provider was not enabled `
- `008-jwt-auth-with-rbac.md` - Remote JWKS fetch is failing with `jwks ConfigMap isn't available`
- `013-advanced-guardrails-webhook.md` - seems like a bug here since they moved from specifying host to only supporting a backendRef
- `018-mcp.md` - fails RBAC when using `mcp.tool.name`, seems like this may have changed to `mcp.resource.name` however when testing with that it also still fails






# 001
```
## 🔥 Breaking Changes

- **GLOO_VERSION default updated**  
  `2.0.1` → `2.1.0-beta.1`

- **Access log schema changed**  
  - `request.body` and `response.body` are now enabled by default (previously commented out)  
  - New default field added: `response.body: json(response.body)`

- **Duplicate log fields introduced**  
  `request.body` appears in both the request and response logging sections, which may change log structure or increase log volume.

- **Image override example removed**  
  The sample image tag (`0.10.3`) was removed and replaced with an empty string, which may affect users who relied on the example for version pinning.
```

# 003
```
## 🔥 Breaking Changes

- **Backend kind changed**  
  `Backend` → `AgentgatewayBackend` (all `backendRefs` must be updated)

- **Spec structure rewritten**  
  - Old: `spec.type: AI` + `spec.ai.llm`  
  - New: `spec.ai.provider` (OpenAI provider block)  
  This layout is **not backward-compatible**.

- **Path field format changed**  
  - Old: `path.full: "/v1/chat/completions"`  
  - New: `path: "/v1/chat/completions"`

- **Auth configuration changed**  
  - Old: `authToken.kind: Passthrough`  
  - New: `policies.auth.passthrough: {}`

- **`host` / `port` relocated**  
  Moved under the new `spec.ai` hierarchy (no longer under `llm`).
```

# 004
```
## 🔥 Breaking Changes

- **Backend kind updated**  
  `Backend` → `AgentgatewayBackend` (must update all `backendRefs`)

- **Spec structure replaced**  
  - Old hierarchy: `spec.type: AI` + `spec.ai.llm.openai`  
  - New hierarchy: `spec.ai.provider.openai`  
  This restructuring is **not backward-compatible**.

- **Auth configuration moved and renamed**  
  - Old:  
    `authToken.kind: SecretRef`  
    `authToken.secretRef.name: openai-secret`  
  - New:  
    `policies.auth.secretRef.name: openai-secret`

- **Removal of `type: AI`**  
  Backend type is now inferred automatically; the explicit field is no longer supported.

- **OpenAI block initialization changed**  
  Old: `llm.openai`  
  New: `provider.openai: {}` (empty map required)
```

