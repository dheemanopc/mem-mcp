export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 prose">
      <h1>Privacy Policy</h1>
      <p><strong>Effective:</strong> 2026-05-03 (closed beta — subject to change before public launch)</p>

      <h2>What we collect</h2>
      <ul>
        <li>Your Google account email and unique sub identifier (via Cognito).</li>
        <li>The memory contents you choose to save (decisions, facts, notes, snippets, questions).</li>
        <li>Tags and metadata you attach to memories.</li>
        <li>Audit log: timestamps and types of operations you perform.</li>
        <li>Operational metadata: connection IP, user agent (for session security only).</li>
      </ul>

      <h2>What we do not collect</h2>
      <ul>
        <li>Browsing history, location, contacts, or any data outside what you explicitly write into mem-mcp.</li>
        <li>We do not share data with third parties (no advertising, no analytics SDKs).</li>
      </ul>

      <h2>Where data lives</h2>
      <p>
        AWS Mumbai (ap-south-1). All data at rest is encrypted (KMS). All transport is HTTPS.
        Backups are encrypted (AES256) and stored in S3 in the same region.
      </p>

      <h2>Your rights (DPDP)</h2>
      <ul>
        <li><strong>Access</strong>: download a JSON dump of all your data via /data/export.</li>
        <li><strong>Erasure</strong>: request deletion via /data/delete. 24h grace period to cancel; full hard-delete within 7 days of confirmation.</li>
        <li><strong>Correction</strong>: edit memories via the dashboard.</li>
        <li><strong>Portability</strong>: same as Access — JSON export.</li>
      </ul>

      <h2>Audit and retention</h2>
      <ul>
        <li>Audit logs retained 730 days, then purged.</li>
        <li>After tenant deletion, audit log entries are anonymized at the 90d mark.</li>
        <li>Memories: retention configurable per-tenant (default 365d). Soft-deleted memories recoverable for 30d.</li>
      </ul>

      <h2>Contact</h2>
      <p>Operator: anand@dheemantech.com</p>
    </main>
  );
}
