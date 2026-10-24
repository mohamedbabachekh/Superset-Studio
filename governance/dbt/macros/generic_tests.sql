{#
    Three generic tests, written here rather than pulled from a package: this kit installs with no internet,
    and `dbt deps` would be the one step that needs it.
#}

{% test accepted_range(model, column_name, min_value=none, max_value=none) %}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} is not null
      and (
            1 = 0
            {% if min_value is not none %} or {{ column_name }} < {{ min_value }} {% endif %}
            {% if max_value is not none %} or {{ column_name }} > {{ max_value }} {% endif %}
          )
{% endtest %}


{% test unique_combination(model, combination_of_columns) %}
    select {{ combination_of_columns | join(', ') }}, count(*) as occurrences
    from {{ model }}
    group by {{ combination_of_columns | join(', ') }}
    having count(*) > 1
{% endtest %}


{#  A join that was meant to add a column must not add or lose a row. #}
{% test equal_rowcount(model, compare_model) %}
    select a.n as model_rows, b.n as compared_rows
    from (select count(*) as n from {{ model }}) a
    cross join (select count(*) as n from {{ compare_model }}) b
    where a.n <> b.n
{% endtest %}
