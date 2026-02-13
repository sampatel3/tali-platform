# Observability Dashboard Definitions

This document defines the minimum operational dashboard for TALI runtime confidence.

## Core SLIs

- **Assessment start failure rate**
  - Definition: failed `POST /api/v1/assessments/token/{token}/start` / total starts
  - Target: < 1% rolling 24h

- **Claude request failure rate**
  - Definition: failed Claude calls / total Claude calls in assessment sessions
  - Target: < 2% rolling 24h

- **Sandbox provisioning latency (p95)**
  - Definition: p95 time from assessment start request until sandbox ready
  - Target: < 8s

- **Scoring computation latency (p95)**
  - Definition: p95 time from submit request to score persisted
  - Target: < 5s


- **Daily spend estimate (USD)**
  - Definition: rolling 24h sum of estimated Claude + E2B + email + storage costs from `/api/v1/billing/costs`
  - Target: below `COST_ALERT_DAILY_SPEND_USD`

- **Cost per completed assessment (USD)**
  - Definition: tenant total estimated cost / completed assessments
  - Target: below `COST_ALERT_PER_COMPLETED_ASSESSMENT_USD`

## Supporting Metrics

- API request error rate by route
- Celery queue depth and task retry counts
- DB connection pool saturation
- Redis availability and latency
- Email send success/failure counts

## Alerting Suggestions

- Page on SLI breach for >15m
- Warn on transient spikes >5m
- Separate alert channels for integrations (E2B, Claude, Workable, Stripe)
