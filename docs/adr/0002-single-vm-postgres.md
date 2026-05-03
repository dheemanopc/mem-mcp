# ADR 0002: Single EC2 VM with co-located Postgres

## Status

Accepted (2026-04-16)

## Context

mem-mcp v1 requires a Postgres database for tenant data storage, audit logs, and memory state. We must decide whether to run Postgres on the same EC2 instance as the application or provision AWS RDS.

RDS offers managed backups, automated failover, and high availability — but at a cost (~$30/month minimum for a db.t4g.small in ap-south-1 with 20 GiB storage). In closed beta, we expect < 10 tenants and < 50 GiB of data. The RDS cost is unjustified given the scale and the need to minimize burn rate.

A single t4g.medium EC2 instance (≈$10/month) can easily run both the mem-mcp application and Postgres, with Caddy (reverse proxy) on the same box for TLS termination and load balancing.

## Decision

v1 deploys Postgres and mem-mcp on a single EC2 t4g.medium instance. High availability and failover are not implemented in v1; this is an acceptable tradeoff for closed beta.

Backup strategy: nightly pg_dump to S3 (via cron + bash script + boto3). This provides point-in-time recovery and avoids the cost of RDS Multi-AZ.

## Consequences

### Positive
- Cost: single instance + S3 storage << RDS + managed standby
- Operational simplicity: no cross-AZ networking, no DNS failover logic
- Faster iteration: SSH directly to the box, adjust Postgres config without CFT redeploy
- Sufficient for v1 scale and closed-beta SLA (no 99.9% uptime requirement)

### Negative
- No automatic failover; instance loss = downtime until manual recovery
- Backup windows block write traffic (pg_dump takes an exclusive lock on database state)
- Storage scaling requires instance replacement or EBS resize
- Postgres config tuning is manual (no AWS RDS management console)

### Risks accepted
- Instance failure = complete service outage. Mitigation: nightly backups to S3; document runbook for recovery.
- Backup dumps are not continuous; in-flight transactions at failure time are lost. Mitigation: RTO ~24h, RPO ~1h for v1 is acceptable.
- No read replicas; all queries must hit the single primary. Mitigation: v2 can adopt RDS with cross-region standby if traffic justifies.

## Alternatives considered

- **AWS RDS (db.t4g.small, Multi-AZ)**: Rejected for v1. Cost is ~$30/month (primary + standby); overkill for closed beta. Revisit for v2 if SLA hardens.
- **Aurora Serverless**: Rejected. Still ~$18/month, billing complexity, and no major advantage at closed-beta scale.
- **DynamoDB**: Rejected. Strongly relational schema (many JOINs, foreign keys); DynamoDB's item-level operations would require significant rewrite.
