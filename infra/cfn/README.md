# CloudFormation Infrastructure

This directory contains CloudFormation templates and configuration for the mem-mcp infrastructure.

## Layout

```
infra/cfn/
├── README.md                           # This file
├── root.yaml                           # Root stack (future PR T-1.10)
├── samconfig.toml                      # SAM CLI config (cp from samconfig.toml.example)
├── nested/                             # Nested stacks
│   ├── 010-network.yaml                # VPC, subnets, IGW, security groups (T-1.1 — this PR)
│   ├── 020-secrets.yaml                # KMS, SSM parameters (T-1.2)
│   ├── 030-storage.yaml                # S3 backup bucket + lifecycle (T-1.3)
│   ├── 040-identity.yaml               # Cognito user pool + Google IdP (T-1.4)
│   ├── 050-lambda-presignup.yaml       # Lambda PreSignUp trigger (T-1.5)
│   ├── 060-compute.yaml                # EC2, IAM instance profile (T-1.6)
│   ├── 070-dns.yaml                    # Route 53 records (T-1.7)
│   ├── 080-observability.yaml          # CloudWatch, alarms, SNS (T-1.8)
│   └── 090-bootstrap-bucket.yaml       # Bootstrap bucket for nested templates + Lambda zips (T-1.0)
├── us-east-1/                          # ACM cert stack (us-east-1 only)
│   └── cert.yaml                       # Cognito custom domain cert (T-1.9)
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
- [ ] **Route 53 hosted zone**: Create or note the ID of existing Route 53 hosted zone for `dheemantech.in`
- [ ] **KMS customer-managed key**: Create KMS CMK with alias `alias/mem-mcp` (required by 090-bootstrap-bucket.yaml)

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
  --capabilities CAPABILITY_NAMED_IAM
```

Wait for stack creation to complete. Note the certificate ARN for Step 4.

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

## Nested Stack Reference

| Stack | Status | Description |
|---|---|---|
| `010-network.yaml` | T-1.1 (this PR) | VPC, subnets, Internet Gateway, route tables, security groups |
| `020-secrets.yaml` | Future PR T-1.2 | KMS CMK, SSM Parameter Store placeholders for secrets |
| `030-storage.yaml` | Future PR T-1.3 | S3 backup bucket, versioning, encryption, lifecycle rules |
| `040-identity.yaml` | Future PR T-1.4 | Cognito user pool, custom domain, Google IdP, web client, resource server |
| `050-lambda-presignup.yaml` | Future PR T-1.5 | Lambda function for Cognito PreSignUp trigger, execution role, permissions |
| `060-compute.yaml` | Future PR T-1.6 | EC2 t4g.medium instance, IAM instance profile, EBS gp3, Elastic IP, termination protection |
| `070-dns.yaml` | Future PR T-1.7 | Route 53 records (A, CNAME) for mem.*, app.*, auth.* subdomains |
| `080-observability.yaml` | Future PR T-1.8 | CloudWatch log groups, custom metrics, alarms, SNS topics, dashboard |
| `us-east-1/cert.yaml` | Future PR T-1.9 | ACM certificate for Cognito custom domain (must be in us-east-1) |
| `root.yaml` | Future PR T-1.10 | Root stack composing all nested stacks via SAM CLI |
| `090-bootstrap-bucket.yaml` | T-1.0 (this PR) | S3 bucket for nested templates and SAM Lambda artifacts (encrypted, versioned, secure transport enforced) |

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
