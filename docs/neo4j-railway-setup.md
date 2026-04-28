# Neo4j on Railway — Setup Guide

The candidate knowledge-graph view and graph predicates in natural-language
search are powered by Neo4j. In production we deploy Neo4j via Railway's
template; locally the integration is **optional** — if `NEO4J_URI` is unset
the graph features degrade gracefully (graph view shows a configuration
hint, NL queries skip graph predicates and run on Postgres alone).

## 1. Provision Neo4j on Railway

1. Open the Railway project that hosts the Tali backend service.
2. Click **+ New** → **Template** → search for **"Neo4j"** → pick the
   community-edition template (it deploys `neo4j:5-community` with bolt
   on port 7687 and the browser on 7474).
3. Railway provisions a service named `neo4j`. After it boots, open the
   service → **Variables** and copy:
   - `NEO4J_AUTH` (format `neo4j/<password>`)
   - The internal bolt URL — Railway exposes it as
     `bolt://${{Neo4j.RAILWAY_PRIVATE_DOMAIN}}:7687` (private domain →
     same-region traffic, no egress fees).

## 2. Wire env vars on the backend service

In the Tali backend service's **Variables** tab, add:

| Variable          | Value                                                  |
| ----------------- | ------------------------------------------------------ |
| `NEO4J_URI`       | `bolt://${{Neo4j.RAILWAY_PRIVATE_DOMAIN}}:7687`        |
| `NEO4J_USER`      | `neo4j`                                                |
| `NEO4J_PASSWORD`  | The password from `NEO4J_AUTH` (the part after `neo4j/`) |
| `NEO4J_DATABASE`  | `neo4j` (default; only change if you create extra DBs) |

Railway re-deploys the backend service automatically on save.

## 3. Verify the connection

After redeploy, hit the health endpoint:

```bash
curl https://<backend-domain>/api/healthz/neo4j
```

Expected: `{"status": "ok", "version": "5.x.x"}`. A `"status":"unconfigured"`
response means env vars aren't picked up; `"status":"error"` means the
backend reached Neo4j but auth failed.

## 4. Backfill existing candidates into the graph

After the connection is healthy, run the one-shot backfill against your
production org from a Railway one-off shell or your local machine pointed
at the production env:

```bash
python -m app.candidate_graph.backfill --org <organization_id>
```

The backfill is idempotent — safe to re-run after schema bumps. It walks
every candidate's `experience_entries` / `education_entries` / `skills`
JSON and upserts the corresponding `:Person`, `:Company`, `:School`,
`:Skill` nodes plus `:WORKED_AT`, `:STUDIED_AT`, `:HAS_SKILL` edges, all
labelled with `organization_id`.

## 5. Multi-tenancy

A single Neo4j instance serves all orgs. Tenancy is enforced at query
time: every Cypher template begins with
`MATCH (n) WHERE n.organization_id = $org_id ...` and every node/edge
created by sync carries `organization_id`. Cross-org traversal is
impossible by construction.

There is no row-level security in Neo4j; the safety guarantee comes
from the [`Neo4jClient`](../backend/app/candidate_graph/client.py)
wrapper, which forces every query to bind `org_id` from the caller's
session. Direct driver use is forbidden by the architecture lints.

## 6. Backups

Railway snapshots the Neo4j volume daily by default. Because Postgres
is the source of truth and the graph is a derived projection, the
Disaster-Recovery story is:

1. Re-provision Neo4j (or fail-over to a fresh instance).
2. Wire the new credentials into the backend.
3. Run `python -m app.candidate_graph.backfill --all-orgs` to rebuild.

Estimated rebuild time at current scale: ~3 minutes per 10k candidates.

## 7. Disabling the integration

If you ever need to take Neo4j down (incident, cost cut, etc.), unset
`NEO4J_URI` on the backend service. The graph view, graph-predicate
NL queries, and the rerank step all degrade silently — list-style NL
search keeps working against Postgres.
