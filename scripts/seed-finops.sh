#!/usr/bin/env bash
# Fill the BigQuery billing table with SYNTHETIC cost data (90 days x 2 environments x 8 SKUs). Deterministic and
# idempotent. This is not real billing data; it exists so the finops MCP server has something to answer questions about.
source "$(dirname "$0")/lib.sh"
need bq
bq query --project_id "$PROJECT_ID" --location EU --use_legacy_sql=false --quiet "
CREATE OR REPLACE TABLE mcp_finops.billing (usage_date DATE, service STRING, sku STRING, project STRING, cost FLOAT64) AS
SELECT d AS usage_date, s.service, s.sku, p AS project,
  ROUND(s.base * IF(p = 'prod', 1.0, 0.3)
        * (0.7 + MOD(ABS(FARM_FINGERPRINT(CONCAT(CAST(d AS STRING), s.sku, p))), 1000) / 1000 * 0.6)
        * (1 + DATE_DIFF(d, DATE_SUB(CURRENT_DATE(), INTERVAL 89 DAY), DAY) / 300), 2) AS cost
FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(CURRENT_DATE(), INTERVAL 89 DAY), CURRENT_DATE())) d,
     UNNEST(['prod', 'staging']) p,
     UNNEST([STRUCT('Compute Engine' AS service, 'N2 Instance Core' AS sku, 42.0 AS base),
             STRUCT('Compute Engine', 'N2 Instance RAM', 18.0), STRUCT('BigQuery', 'Analysis', 27.0),
             STRUCT('BigQuery', 'Active Storage', 6.0), STRUCT('Cloud Storage', 'Standard Storage', 9.5),
             STRUCT('Kubernetes Engine', 'Autopilot Pod vCPU', 31.0), STRUCT('Cloud Logging', 'Log Storage', 4.0),
             STRUCT('Networking', 'Egress Internet', 12.0)]) s" >/dev/null
rows="$(bq query --project_id "$PROJECT_ID" --location EU --use_legacy_sql=false --quiet --format=csv 'SELECT COUNT(*) FROM mcp_finops.billing' | tail -1)"
ok "billing table seeded: $rows synthetic rows"
