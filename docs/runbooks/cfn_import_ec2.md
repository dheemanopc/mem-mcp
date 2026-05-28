# Runbook: Import an existing EC2 instance into ComputeStack

## When to use

CFN state drift after a partial deploy or an out-of-band EC2 swap. Symptom: `aws cloudformation describe-stack-resource --logical-resource-id Ec2Instance` returns a `PhysicalResourceId` for an instance that no longer matches the live working instance (terminated, replaced, or wrong instance).

This runbook reconciles by IMPORTING the live working instance into CFN state, so the recorded resource = the actual resource.

## Pre-flight

- Identify the working instance ID (the one you want CFN to "adopt"). Example: `i-054760e67d8481307`.
- Verify it matches every property the CFT declares: ImageId, InstanceType, KeyName, SubnetId, SecurityGroupIds, IamInstanceProfile, RootVolumeSizeGib, UserData hash. If they differ, CFN import will fail.
- Confirm there are no other stacks managing this instance: `aws cloudformation list-stacks` and search the resources.
- Snapshot the EBS volume before any state change:
  ```bash
  aws ec2 create-snapshot --volume-id $(aws ec2 describe-instances --instance-ids <ID> --query "Reservations[].Instances[].BlockDeviceMappings[?DeviceName=='/dev/sda1'].Ebs.VolumeId" --output text) --description "pre-cfn-import safety snapshot $(date -u +%FT%TZ)" --region ap-south-1
  ```
- Check the current CFN state for the ComputeStack:
  ```bash
  aws cloudformation describe-stack-resource --stack-name $(aws cloudformation describe-stack-resource --stack-name mem-mcp-prod --logical-resource-id ComputeStack --query "StackResourceDetail.PhysicalResourceId" --output text) --logical-resource-id Ec2Instance --region ap-south-1
  ```

## Import steps

### 1. Prepare an "import template"

Copy `infra/cfn/nested/060-compute.yaml` to `/tmp/060-compute-import.yaml`. Remove every resource EXCEPT the `Ec2Instance` resource. Keep all Parameters at top. This template will be uploaded as the new ComputeStack body during the import change-set. (Subsequent deploys will add back the other resources naturally.)

Actually — easier: keep the full template; CFN import targets a specific logical resource and ignores the rest of the template body for the import-only operation.

### 2. Create an import change-set

```bash
aws cloudformation create-change-set \
  --stack-name $(aws cloudformation describe-stack-resource --stack-name mem-mcp-prod --logical-resource-id ComputeStack --query "StackResourceDetail.PhysicalResourceId" --output text) \
  --change-set-name import-ec2-$(date -u +%Y%m%d-%H%M%S) \
  --change-set-type IMPORT \
  --resources-to-import "[{\"ResourceType\":\"AWS::EC2::Instance\",\"LogicalResourceId\":\"Ec2Instance\",\"ResourceIdentifier\":{\"InstanceId\":\"i-054760e67d8481307\"}}]" \
  --template-body file:///tmp/060-compute-import.yaml \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --region ap-south-1
```

### 3. Review the change-set

```bash
aws cloudformation describe-change-set --change-set-name import-ec2-<timestamp> --stack-name <ComputeStack-physical-id> --region ap-south-1 --query "Changes[]"
```

CFN compares the live instance's properties against the template. Confirm:
- Action == "Import"
- ResourceChange.ChangeSetId matches
- No `Replacement: True` in changes
- ResourceIdentification.{InstanceId} == your live instance ID

If any property diverges (e.g., the template says `InstanceType=t4g.large` but the instance is `t4g.medium`), the change-set fails to create — fix the template OR the instance to match BEFORE executing.

### 4. Execute the import

```bash
aws cloudformation execute-change-set --change-set-name import-ec2-<timestamp> --stack-name <ComputeStack-physical-id> --region ap-south-1
```

Wait for `IMPORT_COMPLETE`:
```bash
aws cloudformation wait stack-import-complete --stack-name <ComputeStack-physical-id> --region ap-south-1
```

### 5. Verify

```bash
aws cloudformation describe-stack-resource --stack-name <ComputeStack-physical-id> --logical-resource-id Ec2Instance --region ap-south-1
```

`PhysicalResourceId` should now equal your live instance ID.

Run a `sam deploy --no-execute-changeset` and inspect the diff — CFN should plan no replacements on Ec2Instance.

## Rollback if import fails

The instance is NOT modified by a failed import; only the CFN state attempt is rolled back. You're back where you started. Investigate the property mismatch (CFN error message names the field). Update template OR instance to match, retry.

## Post-import housekeeping

- Delete the EBS snapshot from pre-flight if not needed for retention.
- Update `docs/runbooks/deploy_ledger.md` to note the EC2 is now CFN-tracked under ComputeStack.
- Run a full `sam deploy` to confirm no inadvertent replacements.
