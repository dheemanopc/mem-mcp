# Deploy resource ledger

Running record of every AWS resource (and external dependency) created during the mem-mcp closed-beta deploy. Update after each `aws cloudformation deploy` and after manual operator actions. Used by `destroy.sh` and as the operator's "what's costing money" reference.

**Account**: `172485061306` · **Primary region**: `ap-south-1` · **Cert region**: `us-east-1`

---

## Created outside CFN (manual / one-time)

### KMS

| Resource | ID | Region | Created | Cost/mo | Cleanup |
|---|---|---|---|---|---|
| Customer-managed CMK | `9a7c483d-b85e-473e-976c-6d213bbb5f2d` | ap-south-1 | 2026-05-04 | $1 | `aws kms schedule-key-deletion --key-id <id> --pending-window-in-days 30` |
| Alias | `alias/mem-mcp` | ap-south-1 | 2026-05-04 | — | `aws kms delete-alias --alias-name alias/mem-mcp` |

Rotation: enabled (annual, automatic).

### SSM Parameter Store

All in ap-south-1. Created via direct `put-parameter` (CFN can't create SecureStrings safely). Tier: Standard (FREE up to 10,000 params).

| Name | Type | Source | Status |
|---|---|---|---|
| `/mem-mcp/cognito/google_client_id` | SecureString | Google Cloud Console (T-0.6) | v1, set 2026-05-04 |
| `/mem-mcp/cognito/google_client_secret` | SecureString | Google Cloud Console (T-0.6) | v1, set 2026-05-04 |

Cleanup: `aws ssm delete-parameter --name <name> --region ap-south-1`.

### CloudTrail

| Resource | Name | Region | Cost/mo | Cleanup |
|---|---|---|---|---|
| Multi-region trail | `mem-mcp-trail` | ap-south-1 | FREE (first trail per region) | `aws cloudtrail delete-trail --name mem-mcp-trail` |
| Trail S3 bucket | `aws-cloudtrail-logs-172485061306-mem-mcp` | ap-south-1 | <$0.10 | `aws s3 rb s3://... --force` |
| Bucket policy | (inline on bucket) | — | — | removed with bucket |

### SES

| Resource | Identity | Region | Status | Cleanup |
|---|---|---|---|---|
| Domain identity | `dheemantech.com` | ap-south-1 | DKIM PENDING (3 CNAMEs added at GoDaddy 2026-05-04) | `aws sesv2 delete-email-identity --email-identity dheemantech.com` |
| Email identity (pre-existing) | `anand@dheemantech.com` | ap-south-1 | verified | preserve |

Background poller `b2muueh2o` watches DKIM status; auto-closes T-0.4 on SUCCESS.

### IAM

| Resource | Detail | Cleanup |
|---|---|---|
| Account password policy | 16-char min, all char classes, 365d rotation | `aws iam delete-account-password-policy` |

(Root MFA + IAM user `Local` are pre-existing; not managed by deploy.)

### External (GoDaddy DNS — manually added)

The following records were added by the operator at GoDaddy → `dheemantech.com` (and `.in` after deploy completes). These are NOT in any AWS state — track them here so cleanup is complete.

| Type | Name (host) | Value | Purpose |
|---|---|---|---|
| CNAME | `qyrxkgcwsdvvyv6vbrjfdv4cjjmlwpin._domainkey` | `qyrxkgcwsdvvyv6vbrjfdv4cjjmlwpin.dkim.amazonses.com` | SES DKIM 1/3 |
| CNAME | `ublemcyvykihjhfu3p3o27pdslh4kkyk._domainkey` | `ublemcyvykihjhfu3p3o27pdslh4kkyk.dkim.amazonses.com` | SES DKIM 2/3 |
| CNAME | `bkef34x55wgjgdpkggycc3extxo3vyh5._domainkey` | `bkef34x55wgjgdpkggycc3extxo3vyh5.dkim.amazonses.com` | SES DKIM 3/3 |
| CNAME | `_434fd4bf4ba32b66677575965dbda6e7.memauth` | `_9e01280a2997f661f28688ddb35a58c1.jkddzztszm.acm-validations.aws.` | ACM cert validation (`memauth.dheemantech.in`) |

Pending records (will be added after root-stack deploy):
| Type | Name (host) | Value | Purpose |
|---|---|---|---|
| A | `memsys` | (EIP from CFN output) | MCP server |
| A | `memapp` | (EIP from CFN output) | Web admin UI |
| CNAME | `memauth` | (CloudFront DNS from CFN output) | Cognito Hosted UI |

---

## CFN stacks

| Stack | Region | Status | Created | What it deploys |
|---|---|---|---|---|
| `mem-mcp-cfn-bootstrap` | ap-south-1 | CREATE_COMPLETE | 2026-05-04 14:44 UTC | S3 bucket `mem-mcp-cfn-172485061306-aps1` (holds nested templates + Lambda zips) |
| `mem-mcp-cert-use1` | us-east-1 | CREATE_IN_PROGRESS | 2026-05-04 14:44 UTC | ACM cert `arn:aws:acm:us-east-1:172485061306:certificate/542ab145-9d7d-4c5c-96ee-d3545f6837f9` for `memauth.dheemantech.in` |
| `mem-mcp-prod` | ap-south-1 | NOT YET DEPLOYED | — | Root stack composing 8 nested stacks (010-080) + PreSignUp wiring |

### Nested stacks (deployed via root stack)

These come up automatically when the root stack deploys. Listed for cleanup reference.

| Logical name | Template | Creates |
|---|---|---|
| NetworkStack | 010-network.yaml | VPC, public subnet, IGW, route table, security group |
| SecretsStack | 020-secrets.yaml | KMS alias passthrough, SSM String placeholders for secrets |
| StorageStack | 030-storage.yaml | S3 backup bucket (SSE-KMS, Glacier IR lifecycle) |
| IdentityStack | 040-identity.yaml | Cognito user pool, custom domain, Google IdP, web client, resource server |
| LambdaStack | 050-lambda-presignup.yaml | PreSignUp Lambda + role |
| ComputeStack | 060-compute.yaml | EC2 t4g.medium, IAM instance profile, EIP, EBS gp3, DLM snapshots |
| DnsStack | 070-dns.yaml | (skipped when ManageDns=false; emits Outputs only) |
| ObservabilityStack | 080-observability.yaml | 5 log groups, 12 alarms, SNS topic, dashboard, budget, anomaly |
| (custom resource) | inline in root.yaml | Wires PreSignUp Lambda to Cognito user pool |

---

## Estimated monthly cost (steady state, 1-10 users)

| Component | Estimate |
|---|---|
| EC2 t4g.medium (on-demand) | $24 |
| EBS gp3 (50 GB) | $4 |
| EIP (attached) | $0 |
| S3 backup (encrypted, ~5 GB) | <$1 |
| S3 CloudTrail logs (low volume) | <$0.10 |
| S3 bootstrap bucket | $0 |
| Cognito (closed beta MAU < 50k) | $0 |
| KMS CMK | $1 |
| Bedrock Titan v2 (~10k tokens/day) | <$1 |
| CloudWatch alarms + logs | <$2 |
| Route 53 | $0 (DNS at GoDaddy) |
| SES (low volume) | <$1 |
| **Total** | **~$33-37/mo** |

---

## Cleanup order

If you ever need to destroy, run scripts in this order:

1. `deploy/scripts/destroy.sh` — tears down all CFN stacks (bootstrap, cert, root + nested), in dependency order. ~10 min.
2. Manual SES delete: `aws sesv2 delete-email-identity --email-identity dheemantech.com --region ap-south-1`
3. Manual KMS schedule-deletion (30-day window): `aws kms schedule-key-deletion --key-id 9a7c483d-... --pending-window-in-days 30 --region ap-south-1`
4. Manual SSM delete: 2 Google OAuth params (the rest are inside CFN-managed parameters)
5. Manual CloudTrail delete: `aws cloudtrail delete-trail --name mem-mcp-trail --region ap-south-1` + delete trail S3 bucket
6. Manual GoDaddy: remove the 7 records listed above (DKIM × 3, ACM validation × 1, service records × 3)

Verify zero residual resources: `deploy/scripts/verify_destroy.sh`.
