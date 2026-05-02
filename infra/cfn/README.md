# CloudFormation Infrastructure

This directory contains CloudFormation templates and configuration for the mem-mcp infrastructure.

## Layout

```
infra/cfn/
├── README.md                           # This file
├── root.yaml                           # Root stack (T-1.10 — this PR)
├── samconfig.toml                      # SAM CLI config (cp from samconfig.toml.example)
├── nested/                             # Nested stacks
│   ├── 010-network.yaml                # VPC, subnets, IGW, security groups (T-1.1)
│   ├── 020-secrets.yaml                # KMS, SSM parameters (T-1.2)
│   ├── 030-storage.yaml                # S3 backup bucket + lifecycle (T-1.3)
│   ├── 040-identity.yaml               # Cognito user pool + Google IdP (T-1.4)
│   ├── 050-lambda-presignup.yaml       # Lambda PreSignUp trigger (T-1.5)
│   ├── 060-compute.yaml                # EC2, IAM instance profile (T-1.6, UserData updated in T-1.11)
│   ├── 070-dns.yaml                    # Route 53 records (T-1.7)
│   ├── 080-observability.yaml          # CloudWatch, alarms, SNS (T-1.8 — this PR)
│   └── 090-bootstrap-bucket.yaml       # Bootstrap bucket for nested templates + Lambda zips (T-1.0)
├── us-east-1/                          # ACM cert stack (us-east-1 only)
│   └── cert.yaml                       # Cognito custom domain cert (T-1.9 — this PR)
└── parameters/
    ├── prod.json.example               # Example parameter file for prod
    └── staging.json.example            # Example parameter file for staging (future)
```

See LLD §2.1 for the complete stack hierarchy and dependencies.

## Pre-deploy Checklist

Before deploying, ensure all of the following manual prerequisites are completed:

- [ ] **Bedrock model access**: Log into AWS Console → Bedrock → Model access → Request access to `amazon.titan-embed-text-v2:0`
- [ ] **SES domain identity**: Console → SES → Verified identities → Create identity for `dheemantech.in` (verify via DNS records)
- [ ] **SES sandbox removal**: AWS Support ticket requesting removal from SES sandbox
- [ ] **Google Cloud OAuth client**: Google Cloud Console → Create OAuth 2.0 Web application client; save client ID and secret to SSM SecureString parameters
- [ ] **Domain registration**: Confirm `dheemantech.in` is registered and owned
- [ ] **Route 53 hosted zone**: Create or note the ID of existing Route 53 hosted zone for `dheemantech.in` (operator-managed; its ID is passed as `HostedZoneId` parameter to 070-dns.yaml)
- [ ] **KMS customer-managed key**: Create KMS CMK with alias `alias/mem-mcp` (required by 090-bootstrap-bucket.yaml)

## Known Gaps

- **030-storage IAM role restriction (T-1.6)**: The bucket policy currently denies non-TLS access and enforces SSE-KMS encryption. IAM-role-based access restriction to `mem-mcp-instance-role` is now available. See the TODO comment in 030-storage.yaml bucket policy for how to wire it.
- **FastAPI app missing (T-3.5)**: The mem-mcp.service systemd unit references `mem_mcp.main:app`, which does not yet exist. The service will keep restarting on failure until Phase 3 / T-3.5 ships the application code. This is harmless and expected on first boot.
- **Next.js web missing (Phase 8)**: The mem-web.service systemd unit and web/ directory build are skipped on first bootstrap if web/ is not present. Once Phase 8 ships the web UI, bootstrap will build and start it.
- **DLM snapshot start time is 21:30 UTC = 03:00 IST**: matches spec §14.3 retention windows. If timezone needs adjusting later, change the `Times:` field on `SnapshotPolicy.PolicyDetails.Schedules`.
- **PreSignUp trigger wiring (T-1.10)**: Resolved via inline custom resource in root.yaml. The custom resource calls `cognito-idp:UpdateUserPool` to wire `LambdaConfig.PreSignUp` after both 040-identity and 050-lambda stacks exist. On stack delete, the custom resource removes the LambdaConfig, allowing clean pool deletion.
- **PreSignUp logic (T-4.8)**: handler.py is a STUB that approves all signups. Real `invited_emails` allowlist check lands in T-4.8. Do NOT route real Cognito traffic through this until T-4.8 ships.
- **Log-filter alarms (T-9.6)**: 080-observability creates only the alarms whose metrics exist before app deploy (EC2 status, CPU, Bedrock throttles, custom backup-success metric, monthly budget, cost anomaly). The full spec §14.4 alarm set (App-5xx-rate, Auth-fail-spike, DCR-attempts, Token-reuse, Quota-circuit-breaker, etc.) requires `AWS::Logs::MetricFilter` patterns over log content the app emits — deferred to Phase 9 / T-9.6 once the app is deployed and we know the actual log shapes.
- **CloudWatch disk usage alarm (future)**: Requires the CloudWatch agent running on EC2 with disk plugin. Not yet implemented.

## SecureString Parameters (post-deploy)

The 020-secrets stack creates **String** SSM parameters via CloudFormation. **SecureString** parameters cannot be safely created via CFN (the value would be visible in the template). After the stack deploys, run these commands to populate the SecureStrings:

```bash
KMS_KEY=alias/mem-mcp
REGION=ap-south-1

aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/db/password --value "$(openssl rand -base64 32)" --overwrite
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/db/maint_password --value "$(openssl rand -base64 32)" --overwrite
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/internal/lambda_shared_secret --value "$(openssl rand -base64 32)" --overwrite
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/backup/gpg_passphrase --value "$(openssl rand -base64 48)" --overwrite
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/web/session_secret --value "$(openssl rand -base64 32)" --overwrite
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/web/link_state_secret --value "$(openssl rand -base64 32)" --overwrite

# Google OAuth — paste real values from Google Cloud Console (T-0.6)
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/cognito/google_client_id --value "REPLACE_ME" --overwrite
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/cognito/google_client_secret --value "REPLACE_ME" --overwrite

# Cognito web client secret — paste from 040-identity stack outputs after deploy
aws ssm put-parameter --region $REGION --type SecureString --key-id $KMS_KEY \
  --name /mem-mcp/cognito/web_client_secret --value "REPLACE_ME" --overwrite
```

To rotate any secret, re-run with `--overwrite` and a new value. The application reads SSM at startup, so rotation requires a `systemctl restart mem-mcp`.

## Deploy Order

Follow this sequence exactly:

### Step 1: One-time bootstrap (first deployment only)

Deploy the bootstrap bucket that will hold all nested templates and Lambda artifacts:

```bash
aws cloudformation deploy \
  --template-file infra/cfn/nested/090-bootstrap-bucket.yaml \
  --stack-name mem-mcp-cfn-bootstrap \
  --region ap-south-1 \
  --capabilities CAPABILITY_NAMED_IAM
```

Wait for stack creation to complete.

### Step 2: One-time ACM certificate (first deployment only)

Deploy the TLS certificate for Cognito custom domain (must be in us-east-1):

```bash
aws cloudformation deploy \
  --template-file infra/cfn/us-east-1/cert.yaml \
  --stack-name mem-mcp-cert-use1 \
  --region us-east-1 \
  --parameter-overrides \
    DomainName=dheemantech.in \
    MemAuthSubdomain=memauth \
    HostedZoneId=ZXXXXXXXXXX \
  --capabilities CAPABILITY_NAMED_IAM
```

After this stack reaches CREATE_COMPLETE (~5-10 minutes for DNS propagation), copy the `CertificateArn` output into `infra/cfn/parameters/prod.json` as `UsEast1CertArn`.

### Step 3: Upload nested templates

Sync all nested template files to the bootstrap bucket:

```bash
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-cfn-bootstrap \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`BootstrapBucketName`].OutputValue' \
  --output text)

aws s3 sync infra/cfn/nested/ s3://${BUCKET}/nested/ --region ap-south-1
```

Verify: `aws s3 ls s3://${BUCKET}/nested/`

### Step 4: Deploy root stack (every subsequent deployment)

```bash
sam deploy \
  --template-file infra/cfn/root.yaml \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --s3-bucket ${BUCKET} \
  --s3-prefix sam-pkg \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides $(cat infra/cfn/parameters/prod.json | \
    jq -r 'to_entries | map("\(.key)=\(.value|tostring)") | join(" ")') \
  --no-fail-on-empty-changeset
```

**Cognito custom domain prerequisite:** the us-east-1 cert (T-1.9 / `us-east-1/cert.yaml`) MUST be deployed and validated BEFORE deploying 040-identity.yaml in the root stack. Pass the cert ARN via the `UsEast1CertArn` parameter. The Cognito custom domain creation can take 10-15 minutes (CloudFront propagation).

## Nested Stack Reference

| Stack | Status | Description |
|---|---|---|
| `010-network.yaml` | T-1.1 | VPC, subnets, Internet Gateway, route tables, security groups |
| `020-secrets.yaml` | T-1.2 | KMS CMK, SSM Parameter Store placeholders for secrets |
| `030-storage.yaml` | T-1.3 | S3 backup bucket, versioning, encryption, lifecycle rules |
| `040-identity.yaml` | T-1.4 | Cognito user pool, custom domain, Google IdP, web client, resource server |
| `050-lambda-presignup.yaml` | T-1.5 | Lambda function for Cognito PreSignUp trigger, execution role, permissions |
| `060-compute.yaml` | T-1.6 | EC2 t4g.medium instance, IAM instance profile, EBS gp3, Elastic IP, termination protection, DLM snapshots |
| `070-dns.yaml` | T-1.7 | Route 53 records (A, CNAME) for mem.*, app.*, auth.* subdomains |
| `080-observability.yaml` | T-1.8 | CloudWatch log groups, custom metrics, alarms, SNS topics, dashboard |
| `us-east-1/cert.yaml` | T-1.9 | ACM certificate for Cognito custom domain (must be in us-east-1) |
| `root.yaml` | T-1.10 | Root stack composing 8 nested stacks (010-080) + PreSignUp wiring + SSM bridge |
| `090-bootstrap-bucket.yaml` | T-1.0 | S3 bucket for nested templates and SAM Lambda artifacts (encrypted, versioned, secure transport enforced) |
| Cloud-init + bootstrap scripts | T-1.11 (this PR) | EC2 UserData (cloud-init), bootstrap.sh, deploy.sh, systemd units, Caddyfile |

## Parameter Overrides

All parameters are specified in `infra/cfn/parameters/prod.json` (a copy from `prod.json.example` with real values filled in). The file follows this schema:

```json
{
  "DomainName": "dheemantech.in",
  "MemSysSubdomain": "memsys",
  "MemAppSubdomain": "memapp",
  "MemAuthSubdomain": "memauth",
  "OperatorEmail": "anand@dheemantech.com",
  "OperatorIpCidr": "203.0.113.45/32",
  "Ec2KeyName": "mem-mcp-ops",
  "Ec2InstanceType": "t4g.medium",
  "BackupRetentionDays": "730",
  "LogRetentionDays": "30",
  "AuditLogRetentionDays": "90",
  "BootstrapBucketName": "mem-mcp-cfn-ACCOUNT-aps1",
  "UsEast1CertArn": "arn:aws:acm:us-east-1:...",
  "GoogleClientIdSsmName": "/mem-mcp/cognito/google_client_id",
  "GoogleClientSecretSsmName": "/mem-mcp/cognito/google_client_secret",
  "HostedZoneId": "Z..."
}
```

See `prod.json.example` for the full template with placeholder values. Replace `REPLACE_*` fields with actual values from the pre-deploy checklist.

## Linting

Validate all CloudFormation templates before deployment:

```bash
poetry run cfn-lint infra/cfn/**/*.yaml infra/cfn/us-east-1/*.yaml
```

This is run automatically on every PR and push to main. See `.github/workflows/infra-lint.yml`.

## Destruction

To destroy the entire infrastructure, see `deploy/scripts/destroy.sh` (future PR T-1.12).

For now: manual `aws cloudformation delete-stack` per stack in reverse order. Contact anand@dheemantech.com for runbook details.

## Related

- `MEMORY_MCP_LLD_V1.md` §2 — complete infrastructure layout and sequence diagrams
- `GUIDELINES.md` — deployment best practices and safety gates
- `deploy/scripts/` — auxiliary deployment and backup scripts (future PRs)
