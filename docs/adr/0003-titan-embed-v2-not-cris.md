# ADR 0003: Titan Embed v2 over CRIS / OpenAI / Cohere

## Status

Accepted (2026-04-17)

## Context

mem-mcp uses text embeddings (1024-dimensional vectors) for semantic search across user memories. The embedding model must be available in ap-south-1, affordable, and integrate cleanly with the tenant-scoped IAM model.

Candidates:
- **Amazon Titan Embed Text v2**: Available on Bedrock in ap-south-1, 1024-d output, ~$0.02 per 1M tokens, IAM-scoped access.
- **OpenAI text-embedding-3-small**: $0.02 per 1M tokens, but requires API key + external call; no IAM integration.
- **Cohere API**: Similar cost, same external dependency + API key management.
- **Sentence-Transformers (e.g., all-MiniLM-L6-v2)**: Free / self-hosted, but requires GPU or CPU inference on the EC2 box; adds operational overhead.

## Decision

Use `amazon.titan-embed-text-v2:0` on AWS Bedrock for all embedding operations. Bedrock handles rate-limiting, caching, and scaling transparently. Access is controlled via IAM roles scoped to the mem-mcp task role.

## Consequences

### Positive
- Zero external API key management; all auth is IAM-native
- Bedrock handles concurrency, caching, and throttling
- Consistent latency within ap-south-1 (no cross-region hops)
- Cost is competitive (~$240/month for 1M requests/day)
- Easy to swap models later (Bedrock API is model-agnostic)

### Negative
- Vendor lock-in to AWS; moving to OpenAI / Cohere requires code changes
- Bedrock API throttling may cap throughput (default ~100 requests/sec)
- No model fine-tuning; we're stuck with Titan's out-of-the-box vectors

### Risks accepted
- Bedrock region/model availability changes. Mitigation: monitor AWS service status; v2 can add fallback to Cohere if Bedrock becomes unavailable.

## Alternatives considered

- **Cohere**: Rejected. Same cost as Titan, but adds external API dependency + manual credential rotation.
- **OpenAI**: Rejected. Text-embedding-3-small is slightly cheaper but still requires external calls, API keys, and usage tracking outside IAM.
- **Sentence-Transformers on-box**: Rejected. CPU inference on t4g.medium is slow (100-500ms per batch); GPU inference requires larger instance. Better to offload to Bedrock.
