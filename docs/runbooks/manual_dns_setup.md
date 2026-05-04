# Manual DNS Setup at GoDaddy

When deploying mem-mcp with `ManageDns=false`, the CloudFormation templates do not create Route 53 records. Instead, you manage DNS records at your external provider (e.g., GoDaddy).

This runbook covers adding all required DNS records at GoDaddy for a typical mem-mcp deployment.

## Prerequisites

- AWS CloudFormation stack deployed with `ManageDns=false`
- Access to GoDaddy domain manager (or your DNS provider)
- AWS CLI installed and configured (for retrieving ACM cert validation CNAMEs)
- Domain registered and nameservers pointing to GoDaddy (or your DNS provider)

## Overview: Four Categories of Records

1. **SES DKIM Records (3 CNAME)** — Enable SES to send emails from your domain
2. **ACM Certificate Validation (1 CNAME)** — Prove domain ownership to ACM for TLS cert issuance
3. **Service A Records (2 A)** — Route memsys and memapp subdomains to EC2 Elastic IP
4. **Service CNAME Record (1 CNAME)** — Route memauth subdomain to Cognito CloudFront distribution

Total: **7 records** to add.

---

## Step 1: Retrieve Values from CloudFormation Outputs

After the root stack deploys, export the necessary values:

```bash
STACK_NAME="mem-mcp"  # or your root stack name
REGION="ap-south-1"   # or your deployment region

# Export outputs as environment variables
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' \
  --output text | while read key val; do
  echo "export ${key}=${val}"
done
```

Save the following outputs:
- `MemSysFqdn` (e.g., `memsys.dheemantech.in`)
- `MemAppFqdn` (e.g., `memapp.dheemantech.in`)
- `MemAuthFqdn` (e.g., `memauth.dheemantech.in`)
- `MemSysTargetIp` — Elastic IP (e.g., `13.234.x.x`)
- `MemAppTargetIp` — same Elastic IP
- `MemAuthTargetCname` — CloudFront hostname (e.g., `d1234.cloudfront.net`)
- `ManageDnsActive` — should be `false`

---

## Step 2: Add SES DKIM Records

SES adds a verified sending identity only when DKIM validation completes. You must add three CNAME records that SES generates.

### 2.1 Retrieve DKIM tokens from SES

```bash
REGION="ap-south-1"

aws ses verify-domain-dkim \
  --domain dheemantech.in \
  --region "$REGION"
```

This returns a list of DKIM tokens (e.g., `abcd1234.dkim.amazonses.com`). CloudFormation may have already created this; if `InvalidParameterValue: Duplicate`, SES identity exists and you can skip this.

### 2.2 Get the DKIM CNAMEs to add

```bash
REGION="ap-south-1"

aws sesv2 get-account-details \
  --region "$REGION" \
  --query 'ProductionAccessEnabled' || \
aws ses get-identity-dkim-attributes \
  --identities dheemantech.in \
  --region "$REGION" \
  --query 'DkimAttributes.dheemantech.in.DkimTokens'
```

This outputs three tokens (e.g., `abcd1234`, `efgh5678`, `ijkl9012`). Each token maps to a CNAME record:

| CNAME Name | CNAME Value |
|---|---|
| `abcd1234._domainkey.dheemantech.in` | `abcd1234.dkim.amazonses.com` |
| `efgh5678._domainkey.dheemantech.in` | `efgh5678.dkim.amazonses.com` |
| `ijkl9012._domainkey.dheemantech.in` | `ijkl9012.dkim.amazonses.com` |

### 2.3 Add DKIM CNAMEs to GoDaddy

1. Log in to **GoDaddy Domain Manager** → **Manage DNS** (or equivalent)
2. For each token, create a CNAME record:
   - **Name:** `<token>._domainkey` (e.g., `abcd1234._domainkey`)
   - **Type:** CNAME
   - **Value:** `<token>.dkim.amazonses.com` (e.g., `abcd1234.dkim.amazonses.com`)
   - **TTL:** 3600 (or default)

3. Save and wait 5–15 minutes for propagation.

### 2.4 Verify DKIM

Once propagated, run:

```bash
aws ses verify-domain-dkim \
  --domain dheemantech.in \
  --region ap-south-1
```

Check the AWS SES console → **Verified Identities** → domain name. DKIM verification should show `✓ Verified`.

---

## Step 3: Retrieve and Add ACM Certificate Validation CNAME

The ACM certificate is created in pending validation state until you add the required CNAME record.

### 3.1 Get the certificate ARN

From CloudFormation root stack outputs:
```bash
STACK_NAME="mem-mcp"
REGION="ap-south-1"

CERT_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`UsEast1CertArn`].OutputValue' \
  --output text)

echo "Certificate ARN: $CERT_ARN"
```

### 3.2 Retrieve validation CNAME

```bash
aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region us-east-1 \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Output (example):
```json
{
  "Name": "_abc123.memauth.dheemantech.in.",
  "Type": "CNAME",
  "Value": "_xyz789.acm-validations.aws."
}
```

**Important:** Remove the trailing dot from both `Name` and `Value` when entering in GoDaddy.

### 3.3 Add the ACM validation CNAME to GoDaddy

1. **GoDaddy Domain Manager** → **Manage DNS**
2. Create one CNAME record:
   - **Name:** `_abc123.memauth` (remove trailing dot from ACM output)
   - **Type:** CNAME
   - **Value:** `_xyz789.acm-validations.aws` (remove trailing dot)
   - **TTL:** 300 (low TTL for faster validation)

3. Save. GoDaddy propagates to nameservers within minutes.

### 3.4 Verify ACM certificate issuance

Wait 1–5 minutes for DNS propagation, then check:

```bash
aws acm describe-certificate \
  --certificate-arn "$CERT_ARN" \
  --region us-east-1 \
  --query 'Certificate.Status'
```

When status is `SUCCESS`, the certificate is issued. (If still `PENDING_VALIDATION` after 10 minutes, run `dig` to verify the CNAME propagated; see [Troubleshooting](#troubleshooting).)

---

## Step 4: Add Service A Records (memsys, memapp)

Both memsys and memapp point to the same Elastic IP on EC2.

From CloudFormation root stack outputs, retrieve:
- `MemSysTargetIp` (e.g., `13.234.x.x`)
- `MemAppTargetIp` (same IP)

### 4.1 Add memsys A record

1. **GoDaddy Domain Manager** → **Manage DNS**
2. Create an A record:
   - **Name:** `memsys`
   - **Type:** A
   - **Value:** `13.234.x.x` (your Elastic IP)
   - **TTL:** 300

3. Save.

### 4.2 Add memapp A record

1. Create another A record:
   - **Name:** `memapp`
   - **Type:** A
   - **Value:** `13.234.x.x` (same Elastic IP)
   - **TTL:** 300

2. Save.

---

## Step 5: Add Service CNAME Record (memauth)

The memauth subdomain uses a CNAME to point to the CloudFront distribution hosting Cognito's custom domain.

From CloudFormation root stack outputs, retrieve:
- `MemAuthTargetCname` (e.g., `d1234.cloudfront.net`)

### 5.1 Add memauth CNAME record

1. **GoDaddy Domain Manager** → **Manage DNS**
2. Create a CNAME record:
   - **Name:** `memauth`
   - **Type:** CNAME
   - **Value:** `d1234.cloudfront.net` (your CloudFront hostname from outputs)
   - **TTL:** 300

3. Save.

---

## Verification

### 5.2 Verify all records with DNS tools

Once all records are saved, verify propagation:

```bash
# Check memsys A record
dig memsys.dheemantech.in A +short
# Expected output: 13.234.x.x

# Check memapp A record
dig memapp.dheemantech.in A +short
# Expected output: 13.234.x.x

# Check memauth CNAME record
dig memauth.dheemantech.in CNAME +short
# Expected output: d1234.cloudfront.net

# Check SES DKIM records
dig abcd1234._domainkey.dheemantech.in CNAME +short
# Expected output: abcd1234.dkim.amazonses.com
```

Or use `nslookup`:

```bash
nslookup memsys.dheemantech.in
nslookup memapp.dheemantech.in
nslookup memauth.dheemantech.in
```

---

## Troubleshooting

### Records not propagating

**Problem:** `dig` returns no results even after 15 minutes.

**Solution:**
1. Verify the record exists in GoDaddy (refresh the DNS management page).
2. Check for typos in record names and values (especially trailing dots; GoDaddy strips them automatically).
3. GoDaddy has default TTLs; changes propagate within 10 minutes globally.
4. Clear your local DNS cache (rarely needed):
   ```bash
   # macOS
   sudo dscacheutil -flushcache
   
   # Linux
   sudo systemctl restart systemd-resolved
   ```

### ACM certificate stuck in PENDING_VALIDATION

**Problem:** After adding the validation CNAME, ACM still shows `PENDING_VALIDATION` after 10 minutes.

**Solution:**
1. Double-check the CNAME name and value in GoDaddy match the ACM output exactly (minus trailing dots).
2. Query the CNAME directly:
   ```bash
   dig _abc123.memauth.dheemantech.in CNAME +short
   ```
   Should return `_xyz789.acm-validations.aws.` (exact match to ACM output).
3. If the CNAME resolves but ACM is still pending, wait another 5 minutes (ACM polls every few minutes).
4. If still stuck after 20 minutes, delete the CNAME and re-add it with identical values.

### Subdomain resolves to wrong IP

**Problem:** `dig memsys.dheemantech.in` returns an unexpected IP.

**Solution:**
1. Verify the Elastic IP in CloudFormation outputs is the intended IP.
2. Delete the A record and re-create it with the correct IP.
3. Clear local DNS cache and retry `dig`:
   ```bash
   dig memsys.dheemantech.in A +short +norecurse
   ```

### GoDaddy nameserver changes

**Problem:** You changed nameservers in GoDaddy or your registrar, and records are not resolving.

**Solution:**
1. Verify nameservers point to GoDaddy (or your DNS provider). In GoDaddy domain settings, check **Nameservers** point to GoDaddy's NS servers (e.g., `ns1.godaddy.com`).
2. If using external nameservers, replicate all DNS records at that provider.

### Apex domain apex records

**Note:** This runbook assumes you are managing subdomains (e.g., `memsys.dheemantech.in`). If you need to create records for the apex domain itself (e.g., `dheemantech.in`), use A records (not ALIAS, which is Route 53-specific). GoDaddy's "A" record type handles apex routing.

---

## Rollback / Cleanup

To stop using these DNS records:

1. Delete the 7 records from GoDaddy (SES DKIM, ACM validation, memsys A, memapp A, memauth CNAME).
2. If you later re-enable `ManageDns=true` and migrate to Route 53, CloudFormation will create new records automatically.

---

## Summary

| Record | Name | Type | Value | TTL |
|---|---|---|---|---|
| SES DKIM 1 | `abcd1234._domainkey` | CNAME | `abcd1234.dkim.amazonses.com` | 3600 |
| SES DKIM 2 | `efgh5678._domainkey` | CNAME | `efgh5678.dkim.amazonses.com` | 3600 |
| SES DKIM 3 | `ijkl9012._domainkey` | CNAME | `ijkl9012.dkim.amazonses.com` | 3600 |
| ACM Validation | `_abc123.memauth` | CNAME | `_xyz789.acm-validations.aws` | 300 |
| memsys | `memsys` | A | `13.234.x.x` | 300 |
| memapp | `memapp` | A | `13.234.x.x` | 300 |
| memauth | `memauth` | CNAME | `d1234.cloudfront.net` | 300 |
