# Changelog

0.12.7 - (7-22-26)
---
- Merge `labs/routing/routing-header-matching.md`, `routing-path-per-model.md`, and `routing-query-parameter-matching.md` into `labs/routing/routing-match-types.md`
- Update `README.md` and `tracks/llm-track.md` to point at the consolidated routing-match-types lab
- Merge `labs/rate-limiting/request-based-rate-limiting.md` into `labs/rate-limiting/global-token-rate-limiting.md` as a REQUEST-vs-TOKEN section, and rename it to `labs/rate-limiting/global-rate-limiting.md`
- Update `README.md` and `tracks/llm-track.md` to drop the standalone request-based-rate-limiting link and point at the renamed global-rate-limiting lab
- Merge `labs/installation/airgap/ably7-image-list.md` into `labs/installation/image-list.md` as an "Air-Gap Mirror Reference" section
- Update Solo UI to `0.5.1`
- Replace `/labs/observability/llm-cost-tracking-with-virtual-keys.md` with the latest Cost Management features. Renamed to `/labs/observability/llm-cost-management.md`

0.12.6 - (7-22-26)
---
- Merge `labs/mcp/dynamic-mcp.md` into `labs/mcp/in-cluster-mcp.md`
- Update `README.md`, `tracks/mcp-track.md`, and `labs/mcp/mcp-tool-federation.md` to drop the now-gone standalone Dynamic MCP link
- Standardize MCP Service `appProtocol` on `agentgateway.dev/mcp` repo-wide
- Drop remaining SSE references: `remote-mcp.md`, `openapi-to-mcp-external-api.md`, `openapi-to-mcp-in-cluster.md`, `charts/agentgateway-developer/values.yaml`
- Swap `mcp-byo-grpc-ext-authz.md`'s live `protocol: SSE` backend for `mcp-server-everything` over `StreamableHTTP`

0.12.5 - (7-21-26)
---
- Update Agentgateway version to `v2026.7.0`
- Update Solo UI to `0.5.0`
- Update air-gapped install
- Retarget `labs/upgrades/migrate-v2026.5.x-to-v2026.6.x.md` → `migrate-v2026.5.x-to-v2026.7.x.md`
- Validate `labs/mcp/mcp-eager-auth-auth0-pre-issuance-authz.md` end-to-end on v2026.7.0 and fold refresh-token grant testing into the flow (requires `offline_access` scope + Auth0 "Allow Offline Access")

0.12.4 - (7-20-26)
---
- Add `labs/platform-engineering/networking-architecture.md`

0.12.3 - (7-16-26)
---
- Add `labs/agent-harnesses/claude-desktop-sso-entra.md`: Claude Desktop SSO via Microsoft Entra ID — per-user interactive sign-in validated at the gateway (issuer + audience), with MDM rollout guidance and screenshots

0.12.2 - (7-16-26)
---
- Minor updates to `labs/platform-engineering`

0.12.1 - (7-16-26)
---
- Refactor labs at `labs/platform-engineering` based on internal feedback

0.12.0 - (7-15-26)
---
- Add `labs/platform-engineering/` category: platform/developer separation of concerns via two Helm charts
- Add `charts/agentgateway-platform`: platform-team chart owning the `Gateway`, cost tiers, security baseline (JWT/WAF), observability, and URL space; onboards teams via route delegation (label + namespace + prefix contract)
- Add `charts/agentgateway-developer`: app-team chart for self-serving LLM/MCP endpoints under an assigned path prefix; strict `values.schema.json` has no vocabulary for rate limits, auth, WAF, or logging
- Add `labs/platform-engineering/platform-and-developer-helm-charts-llm.md`: full separation-of-concerns lab — LLM endpoints, escape attempts, one-line team re-tiering, gateway-wide JWT enablement without touching team releases
- Add `labs/platform-engineering/platform-and-developer-helm-charts-mcp.md`: standalone MCP variant — a `type: mcp` endpoint renders an `entMcp` backend, and tier budgets, JWT, and access logging cover MCP tool calls through the parent route
- Add Platform Engineering section to `README.md` (TOC, lab listing, use cases)
- Add "Negative Test: Deny Entra at the Proxy" section to `labs/security/jwt-auth-through-corporate-proxy-entra.md`: block the IdP in Squid and confirm the JWKS fetch fails (`Forbidden` / `TCP_DENIED/403`) instead of falling back to direct egress

0.11.10 - (7-10-26)
---
- Minor updates to `labs/security/jwt-auth-through-corporate-proxy-entra.md`

0.11.9 - (7-10-26)
---
- Add `labs/security/jwt-auth-through-corporate-proxy-okta.md`: routes an external JWKS fetch (Okta) through a corporate forward proxy using agentgateway's `BackendTunnel` (`policies.tunnel.backendRef`)
- Add `labs/security/jwt-auth-through-corporate-proxy-entra.md`: routes an external JWKS fetch (Entra) through a corporate forward proxy using agentgateway's `BackendTunnel` (`policies.tunnel.backendRef`)
- Add `labs/mcp/composable-mcp.md`: Composable MCP lab — one composite MCP tool (`account-brief`) fans a single call out to distinct MCP + HTTP backends and merges the responses into one result
- Updates to the `style-guide.md`
- Complete migration of `AgentgatewayBackend` to `EnterpriseAgentgatewayBackend` across all labs

0.11.8 - (7-8-26)
---
- Add `style-guide.md`: lab-authoring style guide (archetypes, canonical skeleton, command/YAML/callout/cleanup conventions, repo-maintenance checklist) distilled from a full-corpus audit
- Fix `labs/security/jwt-auth-with-rbac.md` stale narrative: `jwt.group` → `jwt.team`, invalid `rbac:` bonus block → `authorization:` (no `rbac` field exists in the CRD schema), and correct the no-matching-route explanation to `x-team: other-team-id`
- Fix `labs/load-testing/llm-load-testing-k6.md`: k6 Job ENDPOINT host `agentgateway.agentgateway-system` → `agentgateway-proxy.agentgateway-system` (old name fails DNS; verified live on v2026.6.3)
- Fix stale Jaeger cross-link `/install-on-openshift/002-...` → `labs/installation/openshift/002-set-up-monitoring-tools-ocp.md` in 12 routing labs and `labs/mcp/in-cluster-mcp.md`
- Fix `lib/jwt/README.md` links to `labs/mcp/mcp-tool-federation.md` (stale since the labs/ reorg)
- Remove dead `$CLAUDE_GATEWAY_IP:4040` substitution note in `labs/agent-harnesses/claude-code.md`
- Rework MCP session-ID capture in `labs/security/opa-authorization.md` and `labs/security/byo-opa-grpc-ext-authz.md` to use a shell variable instead of `/tmp/mcp-headers.txt`
- Pin `grafana/k6:latest` → `grafana/k6:0.54.0` in `labs/upgrades/in-place-rolling-upgrades.md` and `labs/upgrades/blue-green-namespaces.md`
- Add `--ignore-not-found` to Cleanup deletes in `jwt-auth-with-rbac.md`, `virtual-keys.md`, and `evaluate-openai-model-performance.md`
- Update `README.md` Validated on → v2026.6.3; update `labs/installation/system-requirements.md` to the v2026.6.x support matrix (Kubernetes 1.32–1.36, Gateway API 1.3–1.5, Solo UI 0.4.6)
- Move `labs/mcp/keycloak/images/` → `images/keycloak/` and update the 7 screenshot refs in `mcp-eager-auth-keycloak.md`
- Add `labs/mcp/figma-mcp-entra/.figma-creds.env.example` template; reference it in Step 1 and the folder manifest table
- Live-verified on v2026.6.3: both `appProtocol: kgateway.dev/mcp` and `agentgateway.dev/mcp` route MCP through selector-based backends; a Service port with **no** MCP appProtocol is silently not discovered (documented in `style-guide.md` §16)
- Sync agentgateway Grafana dashboard
- Add `labs/upgrades/migrate-v2026.5.x-to-v2026.6.x.md`: version migration guide (v2026.5.x → v2026.6.x) covering the image-registry consolidation, Kubernetes floor bump, prerequisites, downtime, and the exact upgrade commands; validated live on a v2026.5.2 → v2026.6.3 upgrade
- Fix `labs/upgrades/in-place-rolling-upgrades.md` Step 5: replace `helm upgrade --reuse-values` (fails to template the 6.x chart — `.Values.externalSecrets.stores` nil pointer) with an explicit `-f` values file on the OCI chart
- Add the migration guide to the `README.md` Upgrades & Lifecycle section

0.11.7 - (7-6-26)
---
- Add `labs/security/WAF.md`: WAF for agentic traffic (shape/model allow-list, tool-payload abuse, credential block on request/response, layering with `promptGuard`)
- Bump `ENTERPRISE_AGW_VERSION` to `v2026.6.3` across install labs (WAF ships in 6.3)
- Update image lists: add `waf-server`, fix `redis` tag, reconcile Solo UI `0.4.5` section
- Add WAF lab to `README.md` and `tracks/mcp-track.md`
- Add `labs/mcp/mcp-eager-auth-keycloak.md`: eager-OAuth MCP authentication lab using an in-cluster Keycloak with declarative realm import (no external IdP account needed)
- Move Keycloak deployment manifests for `mcp-eager-auth-keycloak.md` from `labs/mcp/keycloak/` to `lib/keycloak/mcp-enterprise/`; update `kubectl apply -k`/`delete -k` pointers in the lab

0.11.6 - (7-1-26)
---
- Add `/labs/mcp/figma-mcp-auth0` configuring MCP auth with Figma (Auth0 + Token-Exchange Elicitation)
- Add `/labs/mcp/figma-mcp-entra`: Entra variant of the Figma MCP lab
- Update README.md table of contents

0.11.5 - (6-24-26)
---
- Add `labs/upgrades/` category covering zero-downtime upgrades
- Add `in-place-rolling-upgrades.md`: validates zero downtime under continuous k6 traffic for short completions, long-lived streaming, and discrete-POST (StreamableHTTP) MCP; explains why persistent long-lived SSE MCP cannot be made zero-downtime by adding replicas (described, not demonstrated)
- Add `blue-green-namespaces.md` and `multi-cluster-upgrades.md` as planned stubs
- Cross-link the rolling-upgrade section of `production-observability-alerting-and-scaling.md` to the new validation lab
- Add "Upgrades & Lifecycle" section to `README.md`
- Update image paths in `claude-code.md` and `claude-desktop.md`
- Implement `blue-green-namespaces.md`: blue/green upgrades via route delegation — a thin edge proxy delegates `/openai` with weighted backendRefs to independent blue and green proxies in separate namespaces; validates zero-downtime cutover and instant rollback under continuous k6 traffic
- Implement `multi-cluster-upgrades.md`: upgrade Enterprise Agentgateway in one cluster while a peer serves the same global LLM over a Solo ambient multicluster mesh

0.11.4 - (6-24-26)
---
- Move all labs into `labs/<category>/` subdirectories and update all relative links in labs, tracks, and README
- Update `virtual-keys.md` lab to use the updated implementation
- Update `llm-cost-tracking-with-virtual-keys.md`
- Remove `api-key-masking.md` lab
- Update `tracks/llm-track.md`

0.11.3 - (6-23-26)
---
- Update `ENTERPRISE_AGW_VERSION` to `v2026.6.1`
- Update `global-token-rate-limiting.md` to cover the following scenarios:
  1. Global shared counter
  2. Per-user via header (CEL)
  3. Multi-header composite (CEL)
  4. JWT claim (jwt.sub)
  5. Tier-based (jwt.plan, nested value descriptors)
  6. IP-based (source.address)
  7. Mixed time windows (two separate RateLimitConfig objects)

0.11.2 - (6-19-26)
---
- Add a JWT claim-based token rate limiting section to `global-token-rate-limiting.md` (CEL on `jwt.sub`, plus a composite `jwt.org`+`jwt.team` example)

0.11.1 - (6-16-26)
---
- Bump to `v2026.6.0` (Kubernetes prereq now `> 1.31`). **Breaking:** the top-level Helm `image.registry`/`image.tag` is now the global default for every chart-managed image — controller, proxy, and auto-provisioned extensions (`ext-auth-service`, `rate-limiter`, `ext-cache`/`redis`); per-image override blocks are removed and `image-list.md` updated to match
- Added `v2026.5.2` branch as an archive of the previous installation setup for airgapped environments
- Add a new air-gap install lab (`airgap/001-airgap.md` + `airgap/ably7-image-list.md`) demonstrating a single-registry private install, linked from the `README`; cross-link the image lists across the `001`, OpenShift, and air-gap labs
- Split parameters by owner across the `001`, OpenShift, and air-gap labs — an operator `agentgateway-shared-extensions` registered as the GatewayClass default (`gatewayClassParametersRefs`, holds `sharedExtensions`) and a developer `agentgateway-config` attached per-Gateway via `spec.infrastructure.parametersRef`; the two merge
- Add an **Uninstall** section to all three labs (reverse-order teardown, including a GatewayClass cleanup step that `helm uninstall` leaves behind)
- Doc cleanup: clarify `parametersRef` apply ordering, drop the stale experimental-CRD note, and harmonize the controller-install image/pull-secret comments across the labs
- Update `SOLO_MANAGEMENT_UI_VERSION` to `0.4.5`

0.11.0 - (6-10-26)
---
- Update wording around experimental channel usage for K8s gateway api, no longer required since previous experimental features graduated to standard in v1.5.0
- Update Grafana dashboard screenshots and descriptions in `002`
- Add `llm.prompt` and `llm.completion` to the tracing config example in `001` as a reference, commented out by default due to performance implications for large prompts or completions
- Add `tracks/llm-track.md` and `tracks/mcp-track.md` — structured learning tracks that map business use cases and value to specific labs; each covers introduction, goals, prerequisites, and a curriculum table organized by use case with links to relevant lab files

0.10.10 - (6-9-26)
---
- Update scenario in `openapi-to-mcp-in-cluster.md` to query known working stripe-mock endpoints to showcase a realistic use case.

0.10.9 - (6-9-26)
---
- Add new lab: `openapi-to-mcp-external-api.md` — expose a live public REST API (Open-Meteo) as MCP tools via `entMcp` + `protocol: OpenAPI` with an OpenAPI 3.0 schema in a ConfigMap; covers target-level `static.policies.tls.sni` to the HTTPS upstream and MCP Inspector + curl validation
- Add new lab: `openapi-to-mcp-in-cluster.md` — expose an in-cluster deployment's OpenAPI spec as MCP tools using the Stripe mock server (`stripe/stripe-mock`); `entMcp` + `protocol: OpenAPI` over plain in-cluster HTTP (no TLS), with `policies.auth.secretRef` injecting the upstream `Authorization: Bearer` header that stripe-mock requires; curated three-operation schema (`listCustomers`, `listCharges`, `retrieveBalance`), MCP Inspector + curl validation; generated tools nest query params under a `query` object
- Update README.md


0.10.8 - (6-9-26)
---
- Updates to agentgateway grafana dashboard
- `byo-opa-grpc-ext-authz.md`: add Part 3 — body-aware OPA policy

0.10.7 - (6-8-26)
---
- `mcp-eager-auth-okta.md` / `mcp-eager-auth-auth0.md` / `mcp-eager-auth-auth0-pre-issuance-authz.md`: add CORS filter on `.well-known/oauth-*/mcp` rules so MCP Inspector's browser-side OAuth discovery preflight passes; troubleshooting row added
- Add new lab: `mcp-tool-federation.md` — federates four real-world MCP servers (arXiv, FRED, SEC EDGAR, BLS) behind one `EnterpriseAgentgatewayBackend` with four label-selector `spec.mcp.targets` and `failureMode: FailOpen`; covers tool-name prefixing (`<service>-<port>_<tool>`), FailOpen drop-one-backend demo, single JWT auth gating the whole union, and a four-persona tool-filtering policy attached to the backend via `spec.backend.mcp.authorization` that uses `mcp.tool.target == "<service>-<port>"` + `jwt.persona` to carve a different `tools/list` per identity (academic / economist / analyst / admin)
- Add `lib/jwt/`: a non-interactive RS256 JWT signer for workshop labs that need to mint tokens with arbitrary claims — `generate-jwt.sh` (reads claims from file or stdin, prints JWT to stdout), committed demo keypair (`private.pem` / `public.pem`) with matching `jwks.json` (kid `workshop-jwt-key-001`, issuer `workshop.solo.io` — deliberately distinct from the `solo.io` issuer used elsewhere so this lab's JWTs don't cross-validate against other labs' policies), four persona claims files under `claims/`, and a usage README
- `README.md`: add "MCP Tool Federation" entry to the MCP (Model Context Protocol) section
- Add new lab: `byo-opa-grpc-ext-authz.md` — standalone OPA as gRPC ext-authz backend, protects LLM + MCP routes; appendix shows OPA bundle alternative

0.10.6 - (6-5-26)
---
- Quick fix for Agentgateway Grafana dashboard

0.10.5 - (6-5-26)
---
- Migrate backend and backend-targeting policy resources across all labs from OSS `agentgateway.dev/v1alpha1` (`AgentgatewayBackend`, and the four `AgentgatewayPolicy` objects in `llm-failover.md` / `llm-failover-advanced.md` that target backends) to `enterpriseagentgateway.solo.io/v1alpha1` (`EnterpriseAgentgatewayBackend`, `EnterpriseAgentgatewayPolicy`)
- Updates to cost tracking section in `lib/observability/agentgateway/agentgateway-grafana-dashboard-v1.json`
- Update `EnterpriseAgentgatewayParameters` in lab `001` to add some metrics tagging examples
- Update lab `002` to server-side apply the grafana dashboard to avoid annotation size conflicts

0.10.4 - (6-3-26)
---
- Add new lab: `llm-failover-advanced.md` — standalone lab with three failover patterns: intra-priority-group failover (per-provider eviction with P2C load balancing inside a group), 5XX server error failover via a CEL `unhealthyCondition`, and a combined end-to-end demo proving intra-group LB + per-provider eviction + inter-group failover work together
- `README.md`: add "Advanced LLM Failover Patterns" entry under the LLM section

0.10.3 - (6-3-26)
---
- Updates to `claude-code.md` lab
- Add new lab: `claude-desktop.md`

0.10.2 - (6-1-26)
---
- Updates to agentgateway grafana dashboard
- Update `ENTERPRISE_AGW_VERSION` to `v2026.5.2`
- `image-list.md`: bump Enterprise Agentgateway header and controller/proxy image tags from `2026.5.0` to `2026.5.2`
- Add new lab: `mcp-tool-mode-search.md` — Enterprise MCP Search tool mode on `EnterpriseAgentgatewayBackend` (`entMcp.toolMode: Search`, `sessionRouting: Stateless`, static target with `host`/`port`/`path: /`); HTTPRoute uses URLRewrite to strip `/mcp/search` to `/`; deploys `mcp-server-everything`, walks the reader through the `get_tool` / `invoke_tool` meta-tools via MCP Inspector + raw JSON-RPC curl; demonstrates backend-scoped per-tool RBAC via `EnterpriseAgentgatewayPolicy.spec.backend.entMcp.authorization` with the gateway-native `mcp.tool.name` CEL attribute
- Add new lab: `mcp-tool-mode-code.md` — Enterprise MCP Code tool mode (`entMcp.toolMode: Code`, `codeMode.timeout: 60s`); single `run_code` tool exposing a typed JS API for upstream MCP tools, executed in a QuickJS sandbox (4 MiB memory / 20 tool calls / 60s wall-clock / 256 KiB stack); raw JSON-RPC walkthrough composes two upstream tools in one script plus a deliberate timeout demo (120s busy-loop exceeding the 60s ceiling); same backend-scoped `mcp.tool.name` RBAC pattern as the search lab
- `README.md`: add "MCP Tool Mode — Search" and "MCP Tool Mode — Code" entries to the MCP (Model Context Protocol) section

0.10.1 - (5-28-26)
---
- `lib/observability/agentgateway-grafana-dashboard-v1.json`: updates to Claude Pricing Table
- `lib/observability/agentgateway-grafana-dashboard-v1.json`: updates to Cost Tracking
- Add `lib/observability/update-dashboard.sh` helper script
- `configure-routing-aws-bedrock.md` and `configure-routing-aws-bedrock-apikey.md`: add Claude Opus 4.7 (`us.anthropic.claude-opus-4-7`) at `/bedrock/opus` and Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`) at `/bedrock/sonnet`; fix existing Haiku 4.5 model ID by prefixing with `us.` cross-region inference profile (required for on-demand invocation of newer Claude models on Bedrock)

0.10.0 - (5-27-26)
---
- Update `image-list.md` with helm pull commands for the various Helm charts
- Rename `load-testing-k6s.md` → `llm-load-testing-k6.md`; update link text in `README.md` to "LLM Load Testing with k6"
- Add new lab: `mcp-load-testing-k6.md` — k6 load testing of MCP traffic through Enterprise AgentGateway; deploys a Python-based multi-arch MCP echo server (`python:3.12-alpine` + ConfigMap); VU test ramps 5→25 concurrent sessions; RPS test ramps 25→50 req/s; both run in 2m; includes smoke test, optional Grafana/Prometheus observation, and cleanup

0.9.9 - (5-26-26)
---
- `002-set-up-ui-and-monitoring-tools.md`: add license key prerequisite (`SOLO_TRIAL_LICENSE_KEY`); split CRD install into separate step using `management-crds` chart with `management-crds.enabled: false` on main chart to avoid ownership conflicts; add Uninstall section
- `image-list.md`: update Solo UI section from `0.4.2` to `0.4.3`

0.9.8 - (5-21-26)
---
- Update `image-list.md`

0.9.7 - (5-21-26)
---
- `image-list.md`: add management UI section with helm chart (`oci://us-docker.pkg.dev/solo-public/solo-enterprise-helm/charts/management`) and images for version `0.4.2`

0.9.6 - (5-21-26)
---
- `claude-code.md`: extend lab to cover both Claude Max / Team Subscription (Option A) and Direct API Key (Option B)
- `README.md`: add new **Agent Harnesses** section; move `claude-code.md` under it; remove from **Agent Frameworks**

0.9.5 - (5-19-26)
---
- Add new doc: `system-requirements.md` — cloud-agnostic system requirements (Kubernetes/Helm/Gateway API/Istio versions, POC and Prod cluster sizing, control-plane and proxy pod resource recommendations, `EnterpriseAgentgatewayParameters` + `Gateway` examples, shared extension server footprint) aligned to `v2026.5.0`
- `README.md`: link `system-requirements.md` from the Prerequisites section


0.9.4 - (5-19-26)
---
- Minor wording fixes and enhancements to existing eager auth labs

0.9.3 - (5-19-26)
---
- Add new lab: `mcp-eager-auth-auth0-pre-issuance-authz.md` — eager-OAuth with Auth0 + new pre-issuance ext_authz hook (`KGW_OAUTH_ISSUER_CONFIG.pre_issuance`, requires `v2026.5.0`); gates token issuance by Auth0 `sub` via gRPC ext-authz, redirects denied users to a configurable URL; multiplexed backend across in-cluster + remote MCP targets
- Minor wording fixes and enhancements to existing eager auth labs
- `README.md`: add new lab to MCP, Security, and Identity & Delegation sections; add pre-issuance bullet to MCP use cases

0.9.2 - (5-18-26)
---
- Update `ENTERPRISE_AGW_VERSION` to `v2026.5.0`. This version introduces a new versioning schema to align with monthly release cadence
- Update `image-list.md`

0.9.1 - (5-18-26)
---
- Add new lab: `configure-inference-routing-with-vllm.md` — Enterprise Agentgateway routing to an in-cluster vLLM pod serving `Qwen/Qwen2.5-0.5B-Instruct` via the Gateway API Inference Extension (`InferencePool` + `llm-d` Endpoint Picker)
- `README.md`: add new top-level **Inference** section to TOC and body between Routing and Security; add inference-routing bullet to Use Cases

0.9.0 - (5-13-26)
---
- Update naming from Gloo UI > Solo UI

0.8.9 - (5-8-26)
---
- Updates to agentgateway observability dashboard at  `/lib/observability/agentgateway-grafana-dashboard-v1.json`

0.8.8 - (5-6-26)
---
- `mcp-eager-auth-okta.md`: rename `GATEWAY_HOST` env var to `OKTA_GATEWAY_HOST` so it can coexist with the Auth0 lab in the same shell rc without one lab overriding the other
- `mcp-eager-auth-auth0.md`: rename `GATEWAY_HOST` env var to `AUTH0_GATEWAY_HOST` for the same reason

0.8.7 - (5-6-26)
---
- Add new lab: `mcp-eager-auth-auth0.md` — MCP eager-OAuth with Auth0; gateway acts as the OAuth Authorization Server (fake DCR with a pre-registered `client_id`/`client_secret`), brokers Auth0's authorization code flow via `/oauth-issuer`, validates Auth0-issued JWTs against Auth0 JWKS at the MCP backend, terminates TLS on a self-signed cert, tests end-to-end against `@modelcontextprotocol/server-everything` in Streamable HTTP mode
- Add new lab: `mcp-eager-auth-okta.md` — Okta equivalent of the eager-OAuth lab against an Okta custom authorization server (`/oauth2/<authz-server-id>/v1/keys`, no trailing slash on issuer)
- `README.md`: add both eager-OAuth labs to MCP (primary), Security, and Identity & Delegation sections with mutual cross-references; add eager-OAuth bullet to MCP use cases

0.8.6 - (5-2-26)
---
- Update `AGW_UI_VERSION` to `0.3.18`
- Add new lab: `openai-audio.md` — proxies OpenAI Audio API (Text-to-Speech and Speech-to-Text) through AgentGateway using `Passthrough` route type; covers TTS with voice/format selection, STT with Whisper transcription, and a round-trip demo (text → audio → text)
- `README.md`: add `openai-audio.md` to Routing section and Use Cases list
- Add new lab: `openai-video.md` — proxies OpenAI Video API (Sora) through AgentGateway using `Passthrough` route type; covers async video generation, polling for completion, and downloading the result
- `README.md`: add `openai-video.md` to Routing section and Use Cases list

0.8.5 - (4-29-26)
---
- Add new lab: `opa-authorization.md` — OPA authorization for LLM and MCP routes using ext-auth with Rego policies stored in ConfigMaps; covers AuthConfig, EnterpriseAgentgatewayPolicy with `entExtAuth`, custom deny bodies/headers, upstream header injection, and shared AuthConfig across multiple HTTPRoutes
- `README.md`: add `opa-authorization.md` to Security section and Use Cases list

0.8.4 - (4-29-26)
---
- Update `llm-failover.md` lab. Now supports additional error codes other than 429

0.8.3 - (4-23-26)
---
- Update `image-list.md` for `v2.3.2`
- Update other areas where we need to update to `v2.3.2`

0.8.2 - (4-23-26)
---
- `001-install-enterprise-agentgateway.md`: bump `ENTERPRISE_AGW_VERSION` to `v2.3.2`
- Add new lab: `configure-routing-aws-bedrock-irsa.md` — configures AWS Bedrock access via EKS IRSA (IAM Roles for Service Accounts) instead of static credentials; covers OIDC provider association, IAM role with scoped trust policy, `EnterpriseAgentgatewayBackend` without `policies.auth`, and `EnterpriseAgentgatewayParameters` service account annotation for automatic credential injection
- `README.md`: add `configure-routing-aws-bedrock-irsa.md` to Routing section under AWS Bedrock entries
- `configure-routing-aws-bedrock-apikey.md`: normalize resource names from `bedrock-*-apikey` to `bedrock-*` and paths from `/bedrock-apikey/*` to `/bedrock/*` to match the other Bedrock labs (secret name `bedrock-apikey-secret` kept distinct)

0.8.1 - (4-21-26)
---
- Add new lab: `llm-byo-grpc-ext-authz.md` — BYO gRPC ext-authz for LLM routes, targeting HTTPRoute-level policy with OpenAI backend
- Add new lab: `mcp-byo-grpc-ext-authz.md` — BYO gRPC ext-authz for MCP routes, targeting HTTPRoute instead of Gateway, with SSE Accept headers and note on combining with `mcpAuthorization` CEL rules

0.8.0 - (4-13-26)
---
- `configure-body-based-routing.md`: change `x-gateway-model-name` extraction to `default(json(request.body).model, '')` to ensure client-supplied headers are always overwritten and the default model is used when no model is specified in the request body
- `001-install-enterprise-agentgateway.md`: bump `ENTERPRISE_AGW_VERSION` to `v2.3.0`

0.7.9 - (4-8-26)
---
- Standardize `kubectl logs` command across all 31 labs to use label selector and `--prefix --tail 20`; drop `| jq` pipes
- `001-install-enterprise-agentgateway.md`: bump `ENTERPRISE_AGW_VERSION` to `v2.3.0-rc.3`
- `001-install-enterprise-agentgateway.md`: replace `rawConfig.config.logging` with a standalone `EnterpriseAgentgatewayPolicy` (`access-logs`) using `frontend.accessLog.attributes.add`; mark section optional with note that enrichment fields are not required by later labs; add "Next Steps" section pointing to `002`
- `install-on-openshift/001-set-up-enterprise-agentgateway-ocp.md`: bump `ENTERPRISE_AGW_VERSION` to `v2.3.0-rc.3`; replace `rawConfig.config.logging` with a standalone `EnterpriseAgentgatewayPolicy` (`access-logs`) using `frontend.accessLog.attributes.add`; mark section optional; add "Next Steps" section pointing to `002`
- `001-install-enterprise-agentgateway.md`: replace `rawConfig.config.tracing` with a standalone `EnterpriseAgentgatewayPolicy` (`tracing`) using `frontend.tracing.backendRef` and `attributes.add`
- `install-on-openshift/001-set-up-enterprise-agentgateway-ocp.md`: replace `rawConfig.config.tracing` with a standalone `EnterpriseAgentgatewayPolicy` (`tracing`) using `frontend.tracing.backendRef` and `attributes.add`; remove now-empty `rawConfig` block entirely

0.7.8 - (4-6-26)
---
- `001-install-enterprise-agentgateway.md`: bump `ENTERPRISE_AGW_VERSION` to `v2.3.0-rc.1`
- Add new lab: `virtual-keys.md` — per-user API key auth with independent token budgets, budget isolation testing, and advanced patterns: multi-tenant virtual keys, tiered budgets via `headersFromMetadataEntry`, and per-user Prometheus observability
- `001-install-enterprise-agentgateway.md`: enable `metrics.fields.add.user_id` from `request.headers["x-user-id"]` in `EnterpriseAgentgatewayParameters` rawConfig to populate `user_id` label on `agentgateway_gen_ai_client_token_usage` metrics
- `README.md`: add `virtual-keys.md` to Security (after API Key Masking) and Rate Limiting (after Global Token-Based Rate Limiting) with mutual cross-references
- Add new lab: `llm-cost-tracking.md` — per-user token usage via access logs and Prometheus PromQL queries for consumption and cumulative cost
- `README.md`: add `llm-cost-tracking.md` to Observability section with `_(see also: Security, Rate Limiting)_` cross-reference
- Add new lab: `mcp-tool-rate-limiting.md` — per-tool rate limiting for MCP traffic using `RateLimitConfig` with a CEL descriptor that extracts the tool name from the JSON-RPC body; limits `get-env` (Print Environment Tool) to 3 calls/min while all other tools are unrestricted; validation via MCP Inspector GUI
- `README.md`: add `mcp-tool-rate-limiting.md` to Rate Limiting and MCP sections with mutual cross-references

0.7.7 - (4-1-26)
---
- Change lab name to `configure-routing-aws-bedrock-titan-embeddings.md` to match other AWS Bedrock labs

0.7.6 - (4-1-26)
---
- `README.md`: add `configure-body-based-routing.md` to Transformations section; add `_(see also: Routing)_` / `_(see also: Transformations)_` cross-references on both entries
- Add new lab: `configure-bedrock-titan-embeddings.md` — routes embedding requests to Amazon Titan Embed Text v2 via `Passthrough` route type; `HTTPRoute` rewrites `/bedrock/titan-embed` to `bedrock-runtime.us-east-1.amazonaws.com/model/amazon.titan-embed-text-v2:0/invoke`
- `README.md`: add `configure-bedrock-titan-embeddings.md` to Routing section under AWS Bedrock entries

0.7.5 - (4-1-26)
---
- Minor README.md formatting
- Add new lab: `production-observability-alerting-and-scaling.md` in Observability section

0.7.4 - (3-31-26)
---
- Add new lab: `configure-body-based-routing.md` — routes requests to OpenAI (`gpt-4o-mini`) or mock LLM (`mock-gpt-4o`) based on the `model` field in the JSON request body; uses `AgentgatewayPolicy` with `phase: PreRouting` to extract `x-gateway-model-name` and `x-gateway-model-status` headers via CEL expressions; `HTTPRoute` header-matches on those headers with a fallback rule for `x-gateway-model-status: unspecified`
- `README.md`: add `configure-body-based-routing.md` to Routing section
- `001-install-enterprise-agentgateway.md`: bump `ENTERPRISE_AGW_VERSION` to `v2.3.0-beta.8`; update tracing `otlpEndpoint` to `solo-enterprise-telemetry-collector.agentgateway-system.svc.cluster.local:4317`
- Rename `002-set-up-monitoring-tools.md` → `002-set-up-ui-and-monitoring-tools.md`; replace Tempo install with Gloo UI (`management` Helm chart, `AGW_UI_VERSION=0.3.18`); remove Tempo datasource from Grafana values; remove Tempo pods from expected output; add "Access Gloo UI" section (port-forward to `solo-enterprise-ui 4000:80`); add `global.image` override comments for Solo-owned images (UI, OTEL collector); move `imagePullSecrets` under `global.imagePullSecrets` (propagates to subcharts); add ClickHouse image override comments with note on missing registry key; update H1 and `README.md` link text; update all cross-references across all lab files
- Update observability callout in all lab files: clarify Grafana provides metrics dashboard, AgentGateway UI provides traces
- Update pre-requisites in all lab files: mark `002` as optional, recommended for observability

0.7.3 - (3-31-26)
---
- `dynamic-mcp.md`: rewrite Step 1 deployment to use plain `mcp-server-everything` (no version labels); update tool names to match current `@modelcontextprotocol/server-everything` release (`get-sum`, `get-env`, `get-tiny-image`); expand Step 4 with dual-pod log tailing and session stickiness/reconnect exercise; remove Key Takeaways section
- `README.md`: list `obo-crewai-agent-with-mcp.md` in both MCP and Identity & Delegation sections with mutual cross-references
- `001-install-enterprise-agentgateway.md`, `install-on-openshift/001-set-up-enterprise-agentgateway-ocp.md`: add commented-out `imagePullSecrets` for all components (controller helm values, agentgateway, extauth, ratelimiter, extCache)
- `002-set-up-monitoring-tools.md`: add commented-out image overrides and `imagePullSecrets` for tempo-distributed (tempo, memcached) and kube-prometheus-stack (grafana, prometheus, prometheusOperator, kube-state-metrics)
- `install-on-openshift/002-set-up-monitoring-tools-ocp.md`: add commented-out image override and `imagePullSecrets` for jaeger allInOne
- `in-cluster-mcp.md`: clarify Bearer token format requires `Bearer <token>` prefix in MCP Inspector; add note to click Reconnect after entering credentials
- `remote-mcp.md`: change HTTPRoute path to `/mcp`; add same Bearer token and Reconnect notes

0.7.2 - (3-31-26)
---
- Add new lab: `dynamic-mcp.md` which covers dynamic MCP backends using label selectors, deploying `mcp-server-everything` to a dedicated `mcp` namespace, scaling example without modifying the `EnterpriseAgentgatewayBackend`
- `in-cluster-mcp.md`: add SSE session affinity limitation callout — explains why AGW proxy must run at 1 replica with SSE transport and links to `dynamic-mcp.md` (Streamable HTTP) as the solution

0.7.1 - (3-30-26)
---
- Reorganize workshop labs: strip numeric prefixes from 36 lab filenames (keeping `001-` and `002-` for installation labs)
- Rewrite `README.md` with 11-section Table of Contents: Installation, Routing, Security, Rate Limiting, Guardrails, Transformations, MCP, Agent Frameworks, Identity & Delegation, Evaluations, Load Testing
- Update cross-references in 17 lab files from "lab NNN" text to named markdown links
- Update `transformations.md` lab

0.7.0 - (3-30-26)
---
- Remove some unnecessary trace fields from `001`
- Update access log attributes in `001`

0.6.9 - (3-18-26)
---
- Add new lab: `036-msft-entra-obo.md` which demonstrates OBO token exchange using Azure Entra ID

0.6.8 - (3-13-26)
---
- `035-obo-crewai-agent-with-mcp.md`: Update lab to reflect agent-performed OBO exchange — login now stores only the Keycloak JWT, agent calls STS at the start of each run
- Update demo walkthrough (Step 11) to match new UI flow: sidebar shows "awaiting agent exchange", live steps log shows STS call, inline token comparison with `iat`/`exp`/`ttl` and raw JWT appears in main area
- Remove data plane proxy restart from Step 5 — replaced with `kubectl rollout status` on control plane only
- Sync `lib/crewai/agentgateway-copilot-with-obo/app.py` with all UI changes: agent STS exchange, `obo_placeholder` separate from `final_placeholder`, timestamp on OBO token, raw JWT display, `timeout=10` on Keycloak and STS requests

0.6.7 - (3-12-26)
---
- Add new lab: `034-obo-token-exchange-fundamentals.md` which demonstrates OBO token exchange using a self-managed keycloak instance
- Add new lab: `035-obo-crewai-agent-with-mcp.md` — CrewAI agent with DeepWiki + Solo.io Docs MCP tools, secured end-to-end with OBO delegation (Keycloak login → agentgateway STS → delegated OBO token, JWT policy on `/openai` and `/agw-copilot/mcp`)
- Add keycloak deployment example in `/lib/keycloak`
- Restructure `lib/`: `lib/crewai/` → `lib/crewai/multi-agent-researcher-writer/`, `lib/langchain/` → `lib/langchain/multi-agent-researcher-writer/`, add `lib/crewai/agentgateway-copilot-with-obo/`
- Update `README.md` with new lab entries and Identity & Delegation use case section
- Update .gitignore

0.6.6 - (3-11-26)
---
- Update `ENTERPRISE_AGW_VERSION` to `v2.2.0`

0.6.5 - (3-11-26)
---
- Update `ENTERPRISE_AGW_VERSION` to `v2.2.0-rc.5`
- Update `013-advanced-guardrails-webhook.md` lab to use `ably7/ai-guardrail-webhook-server:0.1.2`

0.6.4 - (3-10-26)
---
- Extend `008-jwt-auth-with-rbac.md` with PreRouting transformation for JWT claim extraction and header-based routing
- Update `ENTERPRISE_AGW_VERSION` to `v2.2.0-rc.3`
- Update GWAPI CRD version to `v1.5.0` standard instead of experimental channel

0.6.3 - (3-9-26)
---
- Update `install-on-openshift/001-set-up-enterprise-agentgateway-ocp.md`
  - Update `ENTERPRISE_AGW_VERSION` to `v2.2.0-rc.1`
  - Update GWAPI CRD version to `v1.5.0`

0.6.2 - (3-9-26)
---
- Minor updates to `013-advanced-guardrails-webhook.md` lab
- Update `ENTERPRISE_AGW_VERSION` to `v2.2.0-rc.1`
  - Breaking change, we now expect `v` as the prefix to the version
- Update `000-image-list.md`
- Update GWAPI CRD version to `v1.5.0`
- Minor update in `008-jwt-auth-with-rbac.md` to update the text around the dynamic jwt example
- Minor update in `018-in-cluster-mcp.md` to fix npx command
- Minor update in `023-configure-timeouts-and-retries.md` no longer needs to rollout restart to update the AGW proxy replicas
- Minor update in `032-crewai-with-agentgateway.md`
- Minor update in `033-langchain-with-agentgateway.md`

0.6.1 - (3-3-26)
---
- Update `013-advanced-guardrails-webhook.md` lab with more sophisticated scenarios to showcase the value of an LLM-based guardrail over static rules. Use cases now include:
  - Existing innocent request, harassment, jailbreak (regex), PII masking (credit cards), PII masking (email) use cases now fed through an LLM-as-a-judge
  - False positive avoidance - LLM understands context
  - Indirect jailbreak - catches what regex misses
  - Live policy update (add rule about medical advice) via ConfigMap using natural language for new rules

0.6.0 - (3-3-26)
---
- Add new lab: `032-crewai-with-agentgateway.md`
- Add new lab: `033-langchain-with-agentgateway.md`

0.5.9 - (3-2-26)
---
- Fix comments for `EnterpriseAgentgatewayParameters` logging and tracing fields in `001`
- Replaced basic guardrails lab with comprehensive built-in guardrails covering 8 request guards (prompt injection, jailbreak, system prompt extraction, PII, credentials, harmful content, encoding evasion, dangerous advisory) and 2 response masking guards; renamed lab file to `011-builtin-guardrails.md`

0.5.8 - (2-23-26)
---
- Update naming conventions to align with official documentation
  - `agentgateway-system` as the namespace                                                      
  - `agentgateway-proxy` as the name for the proxy defined in the `Gateway` resource              
  - `agentgateway-config` for the name of the `EnterpriseAgentgatewayparameters`

0.5.7 - (2-20-26)
---
- Minor fix in 011: `action: MASK` > `action: Mask` and `action: REJECT` to `action: Reject`
- Added a GIF to README.md

0.5.6 - (2-17-26)
---
- Rename `018` to `018-in-cluster-mcp.md`
- Add new lab: `018a-remote-mcp.md` - Route to external Solo.io docs MCP server through AgentGateway
- Updated agentgateway dashboard at `/lib/observability/agentgateway-grafana-dashboard-v1.json` with additional panels for MCP
- Update `ENTERPRISE_AGW_VERSION` to `2.2.0-beta.1`

0.5.5 - (1-23-26)
---
- Add new lab: `031-SNI-matching.md`
- Update README.md

0.5.4 - (1-22-26)
---
- Comment out enterprise-agentgateway controller image override in `001`
- Remove logging/tracing `rq.headers.all` as this can potentially log sensitive information in the headers like API-keys, recommended to only specifically filter and log required information
- Add new lab: `030-claude-code.md`
- Update README.md

0.5.3 - (1-21-26)
---
- Update `/lib/observability/agentgateway-grafana-dashboard-v1.json` with cost tracking for Claude models

0.5.2 - (1-16-26)
---
- Update `/lib/observability/agentgateway-grafana-dashboard-v1.json` to use a unified dashboard that supports OSS and Enterprise with drop down values
- Update `025-load-testing-with-k6s.md` to use correct model name `mock-gpt-5.2` to match label picked up by grafana dashboard
- Minor update to dashboard description in `002-set-up-monitoring-tools.md`

0.5.1 - (1-15-26)
---
- Update `ENTERPRISE_AGW_VERSION` to `2.1.0`
- Update 000-image-list.md
- Update image overrides with latest

0.5.0 - (1-15-26)
---
- Additional cost tracking panels
  - Cost Rate ($/hour)
  - Projected Monthly Cost (30d)
  - Average Cost Per 1M Requests (Input) by Model
  - Average Cost Per 1M Requests (Output) by Model
  - Average Cost Per 1M Requests (Total) by Model
- Set default pricing values according to [OpenAI Pricing](https://platform.openai.com/docs/pricing)
- Add the mock-gpt-5.2 loadgenerator to `025-load-testing-with-k6s.md` lab
- Update `002-set-up-monitoring-tools.md` with "Agentgateway Dashboard Overview" section with added visuals.

0.4.9 - (1-14-26)
---
- New `029-openai-streaming.md` lab
- Update `/lib/observability/agentgateway-grafana-dashboard-v1.json` with cost tracking

0.4.8 - (1-14-26)
---
- New `028-configure-openai-batches.md` lab
- Extend `024-llm-failover.md` with intra-pool failover example

0.4.7 - (1-14-26)
---
- New lab added for tls termination with agentgateway
- Update naming and numbering
  - `026-tls-termination.md`
  - `027-frontend-mtls.md`

0.4.6 - (1-14-26)
---
- Minor updates before making this repository public

0.4.5 - (1-13-26)
---
- Updated `000-image-list.md` with enterprise agentgateway helm chart update

0.4.4 - (1-13-26)
---
- Added header-based and multi-header rate limiting examples to `016-global-token-based-rate-limiting.md`
- Updated `000-image-list.md` for `2.1.0-rc.1`
- New `026-frontend-mtls.md` lab which is now supported in `2.1.0-rc.1`
- Default to the experimental-install for Gateway API CRDs (required by frontend mTLS lab)
- Use `--server-side` apply when configuring Gateway API CRDs
- `023-configure-timeouts-and-retries.md`: Add retry.backoff configuration examples which is now supported in `2.1.0-rc.1`

0.4.3 - (1-12-26)
---
- Update to `2.1.0-rc.1`
- Update `000-image-list.md`

0.4.2 - (1-8-26)
---
- Update `024-llm-failover.md` to use the mock openai server from `003`

0.4.1 - (1-7-26)
---
- New `025-load-testing-with-k6s.md` lab
- Update README.md use cases section with updates and more detail

0.4.0 - (1-7-26)
---
- Minor updates to `024-llm-failover.md`

0.3.9 - (1-7-26)
---
- New `023-configure-timeouts-and-retries.md` lab
- New `024-llm-failover.md` lab

0.3.8 - (1-7-26)
---
- Quick update to `021a` to verify the active service account

0.3.7 - (1-7-26)
---
- New `021a-configure-basic-routing-to-vertexai-service-account.md` to demonstrate using a GCP service account instead of User Auth for access to VertexAI

0.3.7 - (1-6-26)
---
- Updates to Agentgateway dashboard at `/lib/observability/agentgateway-grafana-dashboard-v1.json`
  - Improved Overview section
  - New Agentgateway Infrastructure Overview row
    - New Agentgateway Data Plane panels
    - New Agentgateway Control Plane panels
- Set `replicas: 2` for agentgateway proxy deployment
- Add CPU/MEM resource requests for agentgateway proxy deployment
- Update `006-configure-routing-to-aws-bedrock.md` to replace titan models with mistral due to EOL of titan models
- New `006a-configure-routing-to-aws-bedrock-apikey.md` to showcase use of short-term and long-term API keys for AWS Bedrock
- Update README.md

0.3.6 - (12-31-25)
---
- Updated mock-openai demos and workshop to use 'mock-gpt-4o' model name for clarity
- Updated Agentgateway dashboard

0.3.5 - (12-30-25)
---
- Add Agentgateway grafana dashboard at `/lib/observability`
- Update `002-set-up-monitoring-tools.md` to include pre-built Agentgateway grafana dashboard setup instructions
- Refactor labs to have a consistent "Observability" section

0.3.4 - (12-29-25)
---
- Fix to policy in `015-local-token-based-rate-limiting.md`
- Add new lab: `022-configure-openai-embeddings.md`

0.3.3 - (12-23-25)
---
- Add new lab: `021-configure-basic-routing-to-vertexai.md` for Google VertexAI routing

0.3.2 - (12-22-25)
---
- Update references of "gloo" to "enterprise agentgateway"

0.3.2 - (12-22-25)
---
- rename `001`

0.3.1 - (12-22-25)
---
- Update lab `001` for Openshift to simplify gateway class usage
- Other minor fixes

0.3.1 - (12-22-25)
---
- Updated lab `001` to simplify gateway class usage by configuring the default gateway class to reference custom `EnterpriseAgentgatewayParameters`, this enables extensibility without defining a new class
- Removed reference to setup in `003` from prerequisites in `004a`, `004b`, and `004c`

0.3.0 - (12-19-25)
  ---
  **Breaking Changes:**
  - Helm chart rename: `gloo-gateway` → `enterprise-agentgateway`, `gloo-gateway-crds` → `enterprise-agentgateway-crds`
  - Helm registry path change: charts now at `oci://us-docker.pkg.dev/solo-public/gloo-gateway/charts/enterprise-agentgateway*`
  - Changed namespace: `gloo-system` → `enterprise-agentgateway`
  - License key flag: `licensing.glooGatewayLicenseKey` / `licensing.agentgatewayLicenseKey` → `licensing.licenseKey`
  - API group migration: `gateway.kgateway.dev` → `agentgateway.dev` for EnterpriseAgentgatewayBackend
  - API group migration: `gloo.solo.io` → `enterpriseagentgateway.solo.io` for policies
  - CRD renames: `Backend` → `EnterpriseAgentgatewayBackend`, `GlooGatewayParameters` → `EnterpriseAgentgatewayParameters`, `GlooTrafficPolicy`/`AgentgatewayEnterprisePolicy` → `EnterpriseAgentgatewayPolicy`
  - GatewayClass name: `agentgateway-enterprise` → `enterprise-agentgateway`
  - Policy structure: traffic fields moved to `spec.traffic`, AI backend fields moved to `spec.backend`
  - Backend structure: `spec.ai.llm` → `spec.ai.provider`, `authToken` → `policies.auth`
  - DirectResponse: separate `DirectResponse` CRD removed, now configured via `AgentgatewayPolicy.spec.traffic.directResponse`

  **Updates:**
  - Updated all labs (001-020) to use new API versions and Helm charts
  - Updated lab 019 to use `AgentgatewayPolicy.spec.traffic.directResponse` instead of separate DirectResponse CRD
  - Reorganized `/install-on-openshift` files to mirror root structure (001: full installation, 002: monitoring tools)
  - Updated OpenShift deployment to use `EnterpriseAgentgatewayParameters` with proper security context configuration
  - Add new lab: `020-configure-basic-routing-to-azureopenai.md` for Azure OpenAI routing
  - Fixed Azure OpenAI endpoint configuration (hostname only, no `https://` scheme)
  - Updated config for OpenShift deploys
  - add `000-image-list.md`
  - add optional air-gapped installation instruction steps to `001`

0.2.3 - (11-21-25)
---
- update `001` to cover the setup of Gloo Gateway control plane and agentgateway in one lab, renamed `001-set-up-gloo-gateway-with-agentgateway.md`
- update `002` to cover the setup of monitoring tools, renamed `002-set-up-monitoring-tools.md`
- updated README to reflect these changes in the TOC
- Added Grafana, Loki, Tempo setup instructions to `002`
- Changed default agentgateway configmap to use Tempo setup, leaving Jaeger config as optionally configurable (commented out in config)
- Add section on how to port-forward to Grafana UI

0.2.2 - (11-21-25)
---
- agentgateway: Added example demonstrating how to label all metrics using a value extracted from the request body (`json(request.body).modelId`) (commented out for now until a later release)
- agentgateway: Added example of capturing full request body in access logs (commented out for now until a later release)
- agentgateway: Added example of capturing `modelId` field from request body in access logs, as an example filtering on specific fields of the request body (commented out for now until a later release)
- agentgateway: add comments in the configmap to describe the behavior above
- Added new lab: `019-configure-direct-response.md` to showcase direct response capabilities


0.2.1 - (11-12-25)
---
- Renamed lab: `017-mcp.md` to `018-mcp.md`
- Added new lab: `017-transformations.md` to showcase transformation capabilities
- Simplify the Helm install values in `install-on-openshift/001`
- Update README.md table of contents

0.2.0 - (11-11-25)
---
- Enhanced lab: `007` API-key masking
- Renamed lab: `017-route-to-mcp-server.md` to `017-mcp.md`
- Enhanced lab: `017-mcp.md` with examples of JWT auth and RBAC on claims and tools using CEL expressions
- Update README.md table of contents

0.1.9 - (11-10-25)
---
- Match OCP logging/tracing config to the standard setup
- Update Gloo Gateway V2 install to use `--set-string` instead of `--set` which matches how license keys for other Solo.io products are documented
- Simplify the Helm install values in `001`
- Added new lab: `003-configure-mock-openai-server.md` to validate core functionality using a mock OpenAI server before testing with OpenAI directly
- Renamed basic routing to OpenAI lab from `003-` to `004-configure-basic-routing-to-openai.md`
- Update README.md table of contents

0.1.8 - (11-5-25)
---
- Capture full request headers (`request.headers`) and JWT claims (`jwt`) in logs/traces.

0.1.7 - (11-4-25)
---
- Update to Gloo Gateway 2.0.1
- Add new lab: `017-route-to-mcp-server.md` for basic demo of MCP connectivity
- change `Gateway` name from `gloo-agentgateway` to `agentgateway` to match docs

0.1.6 - (10-30-25)
---
- Added logging field options to `agent-gateway-config` configmap in `002` to capture all request headers (map or flattened) or extract specific headers. Default set to map with all headers

0.1.5 - (10-20-25)
---
- Rename `015` lab to `016-global-token-based-rate-limiting.md`
- Add new lab: `015-local-token-based-rate-limiting.md` to showcase OSS local token-based rate limiting before moving on to Enterprise global rate limiting

0.1.4 - (10-13-25)
---
- Update `ENTERPRISE_AGW_VERSION=2.0.0`
- Update GWAPI CRD version to `v1.4.0`
- Update /install-on-openshift instructions to remove workarounds required in previous releases
- update `rateLimitConfigRef` to `rateLimitConfigRefs` in 014-request-based-rate-limiting.md to reflect change of API in rc.3
- Add new lab: `015-token-based-rate-limiting.md`
- update `SYSTEM` to `system` in 010-enrich-prompts.md to reflect change of API in rc.3
- Enhanced lab: `008` JWT auth with RBAC policy added to enforce claims in the JWT
- add `service.type: LoadBalancer` to the `GlooGatewayParameters` for agentgateway in `install-base.sh`. This is the default behavior, but explicitly configuring it so that we can see how it is configured if we need to use another service type

0.1.3 - (9-26-25)
---
- Fix cleanup instructions in `004a-path-per-model-routing-example.md`
- Minor fixes to `004b-fixed-path-header-matching-routing-example.md`
- Minor fixes to `004c-fixed-path-queryparameter-matching-routing-example.md`
- Validated that `011-basic-guardrails.md` masking on response works with agentgateway `0.9.0` which will land in GGV2 `rc.2`
- Minor fixes to `013-advanced-guardrails-webhook.md`
- Update `012` external moderation lab to use `GlooTrafficPolicy` instead of `TrafficPolicy`

0.1.2 - (9-26-25)
---
- Update lab numbering in README
- Add section on viewing /metrics endpoint to `003-configure-basic-routing-to-openai.md`
- Update `014-request-based-rate-limiting.md` to have both basic counter and header-based request rate limit examples
- Add new lab: `012-external-moderation-openai-guardrails.md`
- Add "User Stories / Acceptance Criteria" section to the README, these cases will be weaved into the labs over time

0.1.1 - (9-24-25)
---
- Update and test `001` and `002` labs in `/install-on-openshift` using `2.0.0-rc.1`. Validated that all labs are working on OpenShift `4.16.30` which is a current AI GW V2 customer's targeted version
- Change newly added `012` lab to `999-not-working` until next `rc` release
- Add a "required variables" section in `001` labs
- Update README.md

0.1.0 - (9-24-25)
---
- Set `agentgateway.logLevel` to `info` so that tailing access logs is less noisy
- Add instructions on how to view access logs to relevant labs
- Update header for port-forwarding to the Jaeger UI
- Add new lab: `012-configure-per-request-based-rate-limiting.md`

0.0.9 - (9-23-25)
---
- Update `/install-on-openshift` 001 and 002 labs with latest updates from `2.0.0-rc.1`. Still waiting on [Issue #585](https://github.com/solo-io/gloo-gateway/issues/585) to support `floatingUserId` for ext-auth and redis in OpenShift.
- Add new lab: `009-configure-basic-routing-to-anthropic.md` - Thank you to Michael L. for the contribution
- Add new lab: `010-enrich-prompts.md`
- Add new lab: `011-advanced-guardrails-webhook.md`

0.0.8 - (9-22-25)
---
- Update repo to use `2.0.0-rc.1`
- `2.0.0-rc.1` uses `--set` instead of `--set-string` for the license keys in the install. Updated lab to configure `--set licensing.glooGatewayLicenseKey=$SOLO_TRIAL_LICENSE_KEY` and `--set licensing.agentgatewayLicenseKey=$SOLO_TRIAL_LICENSE_KEY`
- Update `agentGateway` to `agentgateway` across the repo
- Remove `GatewayClass` from lab in 002 since this is now automatically generated
- Update agentgateway `gatewayClassName` from `gloo-agentgateway` to `agentgateway-enterprise`
- Configure `infrastructure.parametersRef` in the `Gateway` resource to configure tracing extensions. This was previously handled in the `GatewayClass`, but the user no longer needs to provision this resource
- Update the AI `Backend` resources which switched from `ai.llm.provider.<provider>` to `ai.llm.<provider>` (e.g. `ai.llm.openai`)
- Remove `model` from request body when defined in the AI `Backend` resource. Previously when using model override the client still had to provide `model: ""` but this bug has been fixed

0.0.7 - (9-9-25)
---
- Initial commit of agentgateway on OpenShift deployment (with workarounds) located in the `/install-on-openshift` directory

0.0.6 - (9-9-25)
---
- `008-jwt-auth.md` is now working
- Update cleanup section in `008-jwt-auth.md` lab
- Update README.md

0.0.5 - (9-8-25)
---
- Update Gloo Gateway version to 2.0.0-beta.3
- `007-api-key-masking.md` is now working
- Update curl request format for readability

0.0.4 - (9-4-25)
---
- Update README.md

0.0.3 - (9-4-25)
---
- Add new lab: `005-evaluate-openai-model-performance.md`
- Add new lab: `006-configure-routing-to-aws-bedrock.md`

0.0.2 - (9-4-25)
---
- Update `000-introduction.md` to `README.md`

0.0.1 - (9-4-25)
---
- First commit
  - 001-set-up-gloo-gateway-controller.md
  - 002-configure-agentgateway-with-tracing.md
  - 003-configure-basic-routing-to-openai.md
  - 004a-path-per-model-routing-example.md
  - 004b-fixed-path-header-matching-routing-example.md
  - 004c-fixed-path-queryparameter-matching-routing-example.md