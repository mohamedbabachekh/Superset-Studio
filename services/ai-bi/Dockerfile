# SharpSight AI BI — the service, and the dbt-runner (same image, another command).
#
# What is in it and why:
#   dbt Core + dbt-postgres   the transformation engine; run in a workspace, on the `dev` target, by the service,
#                             and on the `prod` target by the dbt-runner — two roles, two schemas
#   git                       version control for BI projects and for the AI's dbt branches (no remote)
#   Playwright + Chromium     the dashboard is LOOKED at: Superset 6.1's image has no browser, so its own
#                             screenshot endpoints cannot work in this kit. Headless shell only, no full browser
#   sqlglot                   the policy engine reads SQL as a tree, not as text
#
# Nothing here phones home: dbt's usage statistics are off by environment and by dbt_project.yml.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false \
    DO_NOT_TRACK=1

RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && pip freeze > /app-requirements.lock

# The browser and the system libraries it needs, in one layer. --only-shell: the headless shell is a third of
# the size of the full browser and is all a screenshot needs.
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/* && chmod -R a+rX /opt/browsers

# uid 1000, like the kit's Superset. The two state directories exist in the image WITH that owner, so the named
# volumes mounted over them are created owned by it rather than by root.
RUN useradd --uid 1000 --create-home aibi && \
    mkdir -p /app /var/lib/ai-bi /var/lib/dbt-prod /app/exports && \
    mv /app-requirements.lock /app/requirements.lock && \
    chown -R aibi:aibi /app /var/lib/ai-bi /var/lib/dbt-prod

WORKDIR /app
COPY --chown=aibi:aibi aibi/ /app/aibi/
COPY --chown=aibi:aibi tests/ /app/tests/
COPY --chown=aibi:aibi pytest.ini /app/pytest.ini

USER aibi
EXPOSE 8080
CMD ["uvicorn", "aibi.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
