{#
    The QoE score, 1 to 5, from the three KPIs a subscriber actually feels.

    Throughput counts for half and is judged against what the technology can deliver — 6 Mbit/s is excellent
    on 3G and poor on 4G. Latency counts for 30 %, drops for 20 %. Written once, here, so that no model
    carries its own copy of the formula.
#}
{% macro cem_qoe_score(throughput, latency, drop_rate, rat) %}
    round(
        greatest(1.0, least(5.0,
            1.0 + 4.0 * (
                  0.50 * least({{ throughput }} / (case {{ rat }}
                                                      when '5G' then 150.0
                                                      when '4G' then 40.0
                                                      when '3G' then 6.0
                                                      else 0.3
                                                   end), 1.0)
                + 0.30 * (1.0 - least({{ latency }} / 300.0, 1.0))
                + 0.20 * (1.0 - least({{ drop_rate }} / 5.0, 1.0))
            )
        ))::numeric,
        3
    )
{% endmacro %}
