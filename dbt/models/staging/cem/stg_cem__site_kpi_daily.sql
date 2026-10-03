-- The landed extract, renamed and typed. Nothing is computed here.
with source as (

    select * from {{ source('cem_landing', 'site_kpi_daily') }}

)

select
    kpi_date::date                      as date,
    site_id,
    site_name,
    region_name                         as region,
    upper(rat)                          as rat,
    active_users::bigint                as active_users,
    degraded_users::bigint              as degraded_users,
    avg_throughput_mbps::numeric(10, 2) as avg_throughput_mbps,
    avg_latency_ms::numeric(10, 2)      as avg_latency_ms,
    drop_rate_pct::numeric(6, 3)        as drop_rate_pct,
    landed_at
from source
where kpi_date is not null
