# SharpSight Superset DEV KIT — a Superset to build dashboards in, with the drivers the sources need.
#
# THIS IS NOT THE PRODUCT IMAGE. services/superset/Dockerfile builds what customers run: no login screen (the
# gateway authenticates), one read-only connection to the mart, SQL Lab closed to readers, dashboards imported
# from files. This one is the workbench: an ordinary Superset login, SQL Lab open, and connections to whatever a
# developer needs — the CEM's SQL Server, an Oracle, the platform's own mart if they mount a copy.
# Different image name, different compose project, different volumes: nothing here can touch a running platform.
#
# The base is the SAME pinned digest as the product, so this adds no download on a machine that already builds
# SharpSight, and a dashboard drawn here meets the same Superset version it will be installed on.
FROM apache/superset:6.1.0@sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88

USER root

# WITH_JDBC=0 drops Java and the JDBC bridge (a smaller image, no CEM-over-JDBC helper).
ARG WITH_JDBC=1

# The database drivers, in one layer:
#   psycopg2-binary   PostgreSQL — also this instance's own metadata store
#   pymssql           Microsoft SQL Server (the CEM's SQL Server, PBIRS's ReportServer if ever needed): a binary
#                     wheel, so no ODBC driver manager and no unixodbc to install
#   oracledb          Oracle, THIN mode — pure Python, no Instant Client, no LD_LIBRARY_PATH; Superset speaks it
#                     as oracle+oracledb://
#   pyodbc + FreeTDS  THE SECOND ROAD TO SQL SERVER. pymssql is the simple one and needs nothing; it is also
#                     the one that gives up first — an old server, a forced encryption, an Azure endpoint.
#                     ODBC is the road Microsoft's own tooling takes, and FreeTDS is its free driver: in
#                     Debian, no licence click-through, no Microsoft repository to reach — which an
#                     air-gapped machine could not reach anyway
#   PyHive + thrift   Hive / Spark Thrift, for a cluster that exposes port 10000 instead of a JDBC gateway.
#                     Its authentication goes through pure-sasl, not the `sasl` C extension: that one needs a
#                     C++ compiler, which this image has no reason to carry (build failed on it, 2026-09-27)
#   JayDeBeApi/JPype1 the bridge the platform's own gateway uses for the Yacht JDBC driver (cem_to_mart.py)
RUN . /app/.venv/bin/activate && uv pip install --no-cache \
      psycopg2-binary==2.9.9 \
      pymssql==2.3.2 \
      oracledb==2.5.1 \
      PyHive==0.7.0 \
      thrift==0.20.0 \
      thrift-sasl==0.4.3 \
      pure-sasl==0.6.2 \
      JayDeBeApi==1.2.3 \
      JPype1==1.5.0 \
      pandas==2.2.3 \
      pyodbc==5.2.0

# Java, only for the JDBC bridge. Pinned to bookworm's openjdk-17 for the same reason the data gateway pins it:
# the Yacht driver is testable only inside the customer's network, so it does not move to another JVM here.
# krb5-user comes with it: a Kerberised cluster needs a ticket in the JVM's cache before the connection is
# opened, and that is `kinit -kt`, which the image otherwise does not have (cem_to_mart.py runs it).
RUN if [ "$WITH_JDBC" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless krb5-user && \
      rm -rf /var/lib/apt/lists/*; \
    fi
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# The ODBC road to SQL Server: the driver manager, FreeTDS, and the entry that NAMES it. Without that entry
# a URI saying `?driver=FreeTDS` fails with "Can't open lib" and nothing else to go on.
RUN apt-get update && apt-get install -y --no-install-recommends unixodbc odbcinst tdsodbc freetds-bin && \
    rm -rf /var/lib/apt/lists/* && \
    { echo '[FreeTDS]'; \
      echo 'Description = FreeTDS driver for Microsoft SQL Server'; \
      echo 'Driver = /usr/lib/x86_64-linux-gnu/odbc/libtdsodbc.so'; \
      echo 'Setup = /usr/lib/x86_64-linux-gnu/odbc/libtdsS.so'; \
      echo 'FileUsage = 1'; } > /etc/odbcinst.ini

# The checkout keeps Superset's own files in kit/superset/ and the CEM's landing script beside the dbt project it
# feeds (governance/dbt/), but the image keeps them FLAT in /app/pythonpath: superset_config.py imports
# devkit_drivers, devkit_forms, devkit_refresh and (through the drivers) yacht_dialect by bare name, and every
# command in the compose files and the docs says /app/pythonpath/<file>.py. Only the sources moved; the image did not.
COPY --chown=superset:superset kit/superset/superset_config.py kit/superset/devkit_drivers.py kit/superset/devkit_forms.py kit/superset/yacht_dialect.py kit/superset/devkit_seed.py kit/superset/devkit_refresh.py /app/pythonpath/
# The sign-in page: FAB looks templates up by path, so ours only has to come FIRST in the search order
# (superset_config.py puts this folder ahead of the stock one).
COPY --chown=superset:superset kit/superset/templates/ /app/pythonpath/templates/
COPY --chown=superset:superset governance/dbt/cem_to_mart.py /app/pythonpath/
# Its own copy of the brand — the dev kit must LOOK like the product, and copying beats reaching into
# services/superset at build time (this folder builds on a machine that has only this folder).
COPY --chown=superset:superset docs/brand/ /app/superset/static/assets/images/sharpsight/

# The French interface, compiled exactly as the product compiles it (the base image ships .po files only).
COPY kit/superset/sharpsight_translations.py /tmp/sharpsight_translations.py
RUN /app/.venv/bin/python /tmp/sharpsight_translations.py fr && rm /tmp/sharpsight_translations.py

USER superset
