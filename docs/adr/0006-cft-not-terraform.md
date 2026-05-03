# ADR 0006: CloudFormation + SAM over Terraform (v1)

## Status

Accepted (2026-04-20)

## Context

mem-mcp's infrastructure spans EC2, Cognito, IAM, Bedrock, and Postgres. We need infrastructure-as-code to manage these resources reproducibly and enable safe rollback.

Options:
- **AWS CloudFormation + SAM**: AWS-native, no state file infrastructure, IAM templates are clear, rollback is built-in.
- **Terraform**: Supports multi-cloud (future proofing), wide community, but requires separate state store (S3 + DynamoDB) and has a learning curve for AWS-specific resources.

For v1 (single-cloud, closed beta, small team), CFT + SAM are simpler and eliminate the operational burden of managing Terraform state.

## Decision

Use CloudFormation + AWS SAM (Serverless Application Model) for all infrastructure. Organize stacks hierarchically:
- `foundation.yaml`: VPC, subnets, security groups
- `identity.yaml`: Cognito user pool, app client
- `compute.yaml`: EC2 instance, IAM roles, instance profile
- `database.yaml`: RDS or on-box Postgres config (via user data script)

All stacks are deployed via AWS CLI or CodePipeline. State is stored in CloudFormation's built-in stack store (no external S3 bucket required).

## Consequences

### Positive
- Zero state management overhead; CloudFormation tracks stack state natively
- IAM trust policies and assume-role chains are clear in YAML
- Rollback is trivial: `aws cloudformation cancel-update-stack` or `update-stack` to previous template
- AWS-native validation; `cfn-lint` catches many errors before deployment
- No extra tooling (Terraform requires tfstate, remote backend setup)

### Negative
- CloudFormation is more verbose than Terraform (longer YAML files)
- Iteration cycle is slower (5-10 min per CFT update vs. 30 sec for Terraform with cached state)
- If we decide to adopt multi-cloud in v2, we'll need to migrate off CFT

### Risks accepted
- Slower iteration in active development. Mitigation: use local SAM invoke for testing Lambdas before deploying stacks.
- Lock-in to AWS. Mitigation: if multi-cloud becomes a priority, revisit in v2 and accept the migration cost.

## Alternatives considered

- **Terraform**: Rejected for v1. Multi-cloud support is a nice-to-have, but the operational burden (managing state, learning Terraform-specific AWS idioms) outweighs the benefit in a single-cloud project. Revisit in v2 if needed.
- **CDK (TypeScript/Python)**: Rejected. Adds a build step and requires the team to be comfortable with imperative IaC. CFT's declarative model is simpler for a small team.
- **Manual AWS Console clicks + documented runbook**: Rejected. Not reproducible, error-prone, doesn't enable safe rollback.
