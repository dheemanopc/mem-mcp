export default function TermsPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 prose">
      <h1>Terms of Service</h1>
      <p><strong>Effective:</strong> 2026-05-03 (closed beta — subject to change before public launch)</p>

      <h2>1. Beta service</h2>
      <p>mem-mcp is a closed beta. Service is provided as-is. Operator may suspend or
      terminate access at any time for any reason during the beta phase.</p>

      <h2>2. Acceptable use</h2>
      <ul>
        <li>Personal use only during closed beta. No automated scraping, bulk-data ingestion,
            or attempts to access other tenants' data.</li>
        <li>Do not store illegal content, copyrighted works without permission, or sensitive PII
            of third parties.</li>
        <li>Do not use mem-mcp to abuse or attack any third-party AI service or connected client.</li>
      </ul>

      <h2>3. Quotas and rate limits</h2>
      <p>Per spec, default tier limits apply (25k memories, 100k embedding tokens/day). Operator
      may adjust limits per tenant.</p>

      <h2>4. Liability</h2>
      <p>Service is provided WITHOUT WARRANTY. Operator is not liable for data loss, downtime,
      or any consequential damages. Take your own backups via /data/export.</p>

      <h2>5. Termination</h2>
      <p>Either party may terminate at any time. On termination, your data is hard-deleted within
      7 days (DPDP "right to erasure"). You can request export before termination.</p>

      <h2>6. Changes</h2>
      <p>These terms may change before public launch. Operator will notify via email at least 14
      days before any material change.</p>

      <h2>Contact</h2>
      <p>Operator: anand@dheemantech.com</p>
    </main>
  );
}
