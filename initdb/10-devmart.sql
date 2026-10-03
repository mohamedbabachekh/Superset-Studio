-- Two databases, one server: Superset's metadata is not your data.
--
-- `devkit` (created by the image from POSTGRES_DB) is where your extracts live — cem_to_mart.py writes into the
-- devmart schema, CSV uploads and « CREATE TABLE AS » in SQL Lab go to public. `superset` holds the dashboards,
-- charts and users. Wiping one never touches the other.
create database superset owner devkit;

\connect devkit
create schema if not exists devmart authorization devkit;
comment on schema devmart is 'extracts landed by cem_to_mart.py — replaced whole, never appended to';
