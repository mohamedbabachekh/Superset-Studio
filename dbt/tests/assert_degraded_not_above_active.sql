-- A degraded user is an active user. A row saying otherwise means the two counts came from different days.
select date, region, rat, active_users, degraded_users
from {{ ref('mart_cem_qoe_daily') }}
where degraded_users > active_users
