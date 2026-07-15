{{/* Common labels */}}
{{- define "agentgateway-platform.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: agentgateway-platform
{{- end }}

{{/* EnterpriseAgentgatewayParameters name */}}
{{- define "agentgateway-platform.parametersName" -}}
{{ .Values.gateway.name }}-config
{{- end }}

{{/* Contract with agentgateway-developer: path prefix for a team */}}
{{- define "agentgateway-platform.teamPathPrefix" -}}
/teams/{{ . }}
{{- end }}
