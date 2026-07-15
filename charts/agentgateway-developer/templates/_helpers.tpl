{{/* Contract with agentgateway-platform: delegation label + path prefix. */}}
{{- define "agentgateway-developer.team" -}}
{{ required "team is required (assigned by the platform team at onboarding)" .Values.team }}
{{- end }}

{{- define "agentgateway-developer.pathPrefix" -}}
/teams/{{ include "agentgateway-developer.team" . }}
{{- end }}

{{- define "agentgateway-developer.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
team: {{ include "agentgateway-developer.team" . }}
{{- end }}
