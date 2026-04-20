{{/*
Expand the name of the chart.
*/}}
{{- define "harness.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}


{{/*
Create chart name and version as used by the chart label.
Truncated to 63 characrters because Kubernetes label values are limited to this
*/}}
{{- define "harness.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create common labels for the resources managed by this chart.
*/}}
{{- define "harness.labels" -}}
helm.sh/chart: {{ include "harness.chart" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "harness.sanitizeString" -}}
{{- $input := . | lower | replace "." "-" | replace "/" "-" -}}
{{- $input -}}
{{- end -}}