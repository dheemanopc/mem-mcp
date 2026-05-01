-- RLS fail-closed verification smoke test
--
-- Usage: psql -h /var/run/postgresql -U mem_app -d mem_mcp -f deploy/postgres/smoke_rls.sql
--
-- Verifies that RLS is properly enforced: without explicitly setting app.current_tenant_id,
-- the mem_app role cannot see any tenant data per LLD §5.4 / spec S-3.

-- Verify no tenant_id is set
SELECT current_setting('app.current_tenant_id', true) as tenant_context;

-- Verify RLS fail-closed: should return 0 rows without SET LOCAL
DO $$
BEGIN
    IF (SELECT count(*) FROM memories) <> 0 THEN
        RAISE EXCEPTION 'RLS LEAK: memories table visible without tenant context';
    END IF;
    IF (SELECT count(*) FROM tenant_daily_usage) <> 0 THEN
        RAISE EXCEPTION 'RLS LEAK: tenant_daily_usage visible without tenant context';
    END IF;
    RAISE NOTICE 'OK: RLS fail-closed verified - no data leakage without tenant context';
END
$$;
