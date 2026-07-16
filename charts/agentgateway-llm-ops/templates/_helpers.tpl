{{/* Common labels */}}
{{- define "agentgateway-llm-ops.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: agentgateway-llm-ops
{{- end }}

{{/* EnterpriseAgentgatewayParameters name */}}
{{- define "agentgateway-llm-ops.parametersName" -}}
{{ .Values.gateway.name }}-config
{{- end }}

{{/* Label key marking a Secret as a member of an alias's API-key set.
     The per-alias auth policy selects Secrets by this label. */}}
{{- define "agentgateway-llm-ops.aliasLabel" -}}
llm-ops.agentgateway.solo.io/alias-{{ . }}
{{- end }}
