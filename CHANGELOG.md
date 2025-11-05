# Changelog

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
- Update `GLOO_VERSION=2.0.0`
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
- `2.0.0-rc.1` uses `--set` instead of `--set-string` for the license keys in the install. Updated lab to configure `--set licensing.glooGatewayLicenseKey=$GLOO_TRIAL_LICENSE_KEY` and `--set licensing.agentgatewayLicenseKey=$GLOO_TRIAL_LICENSE_KEY`
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