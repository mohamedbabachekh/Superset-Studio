-- The certified mart: day, region, technology. Sites are gone; what is left is safe to put on a dashboard.
--
-- The score is weighted by users HERE and stays weightable afterwards: a chart recomputes
-- sum(qoe_score * active_users) / sum(active_users) over whatever it groups by (metric cem.qoe_score).
select
    date,
    region,
    rat,
    round(sum(qoe_score * active_users) / nullif(sum(active_users), 0), 3)::numeric(5, 3) as qoe_score,
    sum(active_users)::bigint   as active_users,
    sum(degraded_users)::bigint as degraded_users,
    current_timestamp           as loaded_at
from {{ ref('int_cem__qoe_site_daily') }}
group by date, region, rat
