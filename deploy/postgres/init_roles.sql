-- Initialize database roles for mem-mcp
--
-- Usage: psql -v mem_app_password='...' -v mem_maint_password='...' -f init_roles.sql
--
-- Run once as the postgres superuser via local socket on a fresh DB cluster.
-- This script creates the mem_app and mem_maint roles, the mem_mcp database,
-- and configures default privileges.

CREATE ROLE mem_app   LOGIN PASSWORD :'mem_app_password';
CREATE ROLE mem_maint LOGIN PASSWORD :'mem_maint_password' BYPASSRLS;
CREATE DATABASE mem_mcp OWNER mem_maint;

\connect mem_mcp

GRANT CONNECT ON DATABASE mem_mcp TO mem_app;
GRANT USAGE ON SCHEMA public TO mem_app, mem_maint;

ALTER DEFAULT PRIVILEGES FOR ROLE mem_maint IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mem_app;
ALTER DEFAULT PRIVILEGES FOR ROLE mem_maint IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO mem_app;
