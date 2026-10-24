For a Kerberised CEM only.

Put krb5.conf and the service account's keytab here. The folder is mounted read-only at /run/krb5 inside the
container, and cem_to_mart.py runs kinit with them when CEM_KERBEROS=1 (CEM_KRB_PRINCIPAL, CEM_KRB_KEYTAB).

Neither file is committed and neither travels in the bundle: they are the customer's credentials.
