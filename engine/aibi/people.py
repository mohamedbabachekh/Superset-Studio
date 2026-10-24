"""People, in the BI engine: who holds which SharpSight role there.

The engine grants a dashboard to ROLES (tools.apply_superset_access). A role opens nothing until a person
holds it, and who holds what is written in one place — governance/registry/security/rbac.yaml, standing in for the
portal's RBAC. This module carries that to Superset's accounts, and only that:

  · `sync`   every account whose name is a user of rbac.yaml is given exactly the SharpSight roles that user
             has there, no more and no fewer; an account that is NOT in rbac.yaml loses any it holds. Whatever
             else an account holds in the BI engine (its base role, an admin's rights) is left alone.
  · `create` an account for a user of rbac.yaml that has none yet, with the engine's base role and its
             SharpSight roles, and a password shown ONCE to whoever asked, never stored and never logged.

It is a STAND-IN. In the target architecture identities come from Keycloak and nobody types this command;
the mapping from a person's SharpSight roles to the BI engine's is the part that stays.

The engine's own accounts are never touched: the service account, and the probes that exist to hold one
role each on purpose.
"""
from __future__ import annotations

import secrets
from typing import Any

from .audit import AuditLog
from .policy_engine import Identity, PolicyEngine, PolicyError
from .registry import Registry
from .superset_adapter import SupersetAdapter

ROLE_PREFIX = "sharpsight_"
BASE_ROLE = "Gamma"
# The two dashboards Superset draws over the engine's own records (superset_view.py) have an audience too:
# those who work in the factory, and those who may read the audit trail. Capabilities, as everywhere else.
WORKS_IN_THE_FACTORY = ("bi.generate", "bi.approve_uat", "bi.approve_prod")
READS_THE_TRAIL = ("audit.read",)
# Those who generate and commit do it from the SharpSight panel, which Superset 6.1 can only put in SQL Lab
# (studio/extension/): their account is given Superset's own `sql_lab` role, so that the panel is somewhere
# they can go. It opens SQL Lab's page and nothing in it: no database is granted with it. Given, and never
# taken back by a sync — it is Superset's role, and an administrator may have handed it out for other reasons.
SQL_LAB_ROLE = "sql_lab"
WORKS_IN_SQL_LAB = ("bi.generate",)


class People:
    def __init__(self, registry: Registry, policy: PolicyEngine, superset: SupersetAdapter, audit: AuditLog,
                 service_account: str):
        self.registry = registry
        self.policy = policy
        self.superset = superset
        self.audit = audit
        self.service_account = service_account

    def engine_roles(self, username: str) -> list[str]:
        """The BI-engine roles a user of rbac.yaml should hold: one per SharpSight role of theirs that can be
        a dashboard's audience — it may read, and is granted at least one dataset; or it is the audience of
        one of the two dashboards over the engine's own records."""
        user = self.registry.rbac.users.get(username)
        if user is None or user.type != "human":
            return []

        def an_audience(name: str) -> bool:
            role = self.registry.rbac.roles[name]
            return ("bi.read" in role.capabilities and bool(role.datasets)
                    or any(c in role.capabilities for c in WORKS_IN_THE_FACTORY + READS_THE_TRAIL))
        return sorted(ROLE_PREFIX + name for name in user.roles if an_audience(name))

    def works_in_sql_lab(self, username: str) -> bool:
        user = self.registry.rbac.users.get(username)
        return user is not None and user.type == "human" and any(
            capability in self.registry.rbac.roles[name].capabilities
            for name in user.roles for capability in WORKS_IN_SQL_LAB)

    def _is_engines_own(self, username: str) -> bool:
        return username == self.service_account or username.startswith("sharpsight-probe-")

    def sync(self, identity: Identity, request_id: str) -> dict[str, Any]:
        self.policy.require(identity, "access.manage")
        changed, unchanged = [], []
        accounts = {a["username"]: a for a in self.superset.list_accounts()}
        for username, account in sorted(accounts.items()):
            if self._is_engines_own(username):
                continue
            held = sorted(r["name"] for r in account["roles"] if r["name"].startswith(ROLE_PREFIX))
            wanted = self.engine_roles(username)
            lab = self.works_in_sql_lab(username) and SQL_LAB_ROLE not in {r["name"] for r in account["roles"]}
            if held == wanted and not lab:
                if wanted:
                    unchanged.append({"account": username, "roles": wanted})
                continue
            others = [r["id"] for r in account["roles"] if not r["name"].startswith(ROLE_PREFIX)]
            if wanted and not others:                       # a role opens a dashboard; the base role opens the door
                others = [self.superset.find_role(BASE_ROLE)]
            if lab:
                others.append(self.superset.find_role(SQL_LAB_ROLE))
            self.superset.set_account_roles(account["id"], others + [self.superset.ensure_role(r) for r in wanted])
            changed.append({"account": username, "granted": sorted(set(wanted) - set(held)) + ([SQL_LAB_ROLE] if lab else []),
                            "withdrawn": sorted(set(held) - set(wanted)),
                            "why": "as rbac.yaml says" if username in self.registry.rbac.users
                            else "not a user of rbac.yaml"})
        missing = sorted(u for u, body in self.registry.rbac.users.items()
                         if body.type == "human" and u not in accounts and self.engine_roles(u))
        self.audit.record(action="people:sync", user=identity.user, request_id=request_id, actor="human",
                          superset_assets=[{"type": "account", "action": "roles set", **c} for c in changed],
                          detail={"changed": len(changed), "unchanged": len(unchanged), "without_an_account": missing})
        return {"changed": changed, "unchanged": unchanged, "without_an_account": missing}

    def create(self, identity: Identity, username: str, request_id: str) -> dict[str, Any]:
        self.policy.require(identity, "access.manage")
        user = self.registry.rbac.users.get(username)
        if user is None or user.type != "human":
            raise PolicyError(f"{username} is not a person in rbac.yaml: an account is only made for one who is",
                              "unknown_user")
        if any(a["username"] == username for a in self.superset.list_accounts()):
            raise PolicyError(f"{username} already has an account in the BI engine; `people` brings its roles "
                              "up to date", "exists")
        roles = self.engine_roles(username)
        base = [BASE_ROLE] + ([SQL_LAB_ROLE] if self.works_in_sql_lab(username) else [])
        password = secrets.token_urlsafe(12)
        first, _, last = username.partition(".")
        self.superset.create_account(
            username, first.capitalize() or username, last.capitalize() or "-", f"{username}@sharpsight.local",
            [self.superset.find_role(name) for name in base] + [self.superset.ensure_role(r) for r in roles], password)
        # the password goes to the caller and nowhere else: it is not in this record
        self.audit.record(action="people:create", user=identity.user, request_id=request_id, actor="human",
                          superset_assets=[{"type": "account", "action": "created", "account": username,
                                            "roles": [*base, *roles]}])
        return {"account": username, "roles": [*base, *roles], "password": password,
                "note": "shown once; change it in the BI engine after signing in"}
