-- The QoE score, computed once, at the grain it is measured: day, site, technology.
select
    date,
    site_id,
    site_name,
    region,
    rat,
    active_users,
    degraded_users,
    avg_throughput_mbps,
    avg_latency_ms,
    drop_rate_pct,
    {{ cem_qoe_score('avg_throughput_mbps', 'avg_latency_ms', 'drop_rate_pct', 'rat') }} as qoe_score
from {{ ref('stg_cem__site_kpi_daily') }}
