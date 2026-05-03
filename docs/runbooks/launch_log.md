# Launch log

T-10.1 onwards. Append entries as the closed beta progresses. Newest at top.

## Template

```
### YYYY-MM-DD — <event title>

**Phase**: T-10.x
**Operator**: anand
**Build SHA**: <git sha>

**What happened**:
- <bullet>

**Issues encountered**:
- <none / bullet>

**Action taken**:
- <none / bullet>

**Follow-up**:
- <none / TODO>
```

## Entries

(empty until T-10.1 fires — first invitee = operator self)

### Pre-launch checklist (run before T-10.1)

- [ ] All Phase 1-9 PRs merged to main
- [ ] EC2 deployed and `/readyz` returns 200
- [ ] CloudWatch alarms green
- [ ] Backup ran at least once and is restorable
- [ ] Web UI accessible at https://memapp.dheemantech.in
- [ ] DNS resolution OK on memsys / memauth subdomains
- [ ] PRIVACY + TERMS pages drafted (T-8.15)
- [ ] Cognito Hosted UI login flow tested end-to-end
