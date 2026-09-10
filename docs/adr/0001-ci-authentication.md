<!-- docs/adr/0001-ci-authentication.md -->
# docs/adr/0001-ci-authentication.md

# ADR 0001: How CI authenticates to Databricks

Status: accepted
Date: 2026-09-10

## Context

Three of the sixteen quality gates need a live Databricks workspace: `env parity`
reads the environment version the bundle declares, `prereqs` lists catalogs, and
`test (integration)` resolves the bundle. On a laptop they authenticate through
the CLI's DEFAULT profile and an OAuth session in the OS keyring. A GitHub
Actions runner has neither.

The first CI run after those gates were added failed all three at once. That was
the gate working: it reported every failure in one pass rather than one per
cycle, and the cause was structural — the workflow gave CI no Databricks
identity at all.

## What 2026 prescribes

Databricks OAuth token federation (OIDC) is the current answer and it is GA.
Workloads outside Databricks authenticate with a short-lived token minted by
GitHub and exchanged for a Databricks OAuth token, so **no Databricks secret
exists anywhere**. The workflow sets three non-secret environment variables:

    DATABRICKS_AUTH_TYPE: github-oidc
    DATABRICKS_HOST:      <workspace url>
    DATABRICKS_CLIENT_ID: <service principal application id>

That would eliminate secret storage, the rotation task, and the class of
accident where a credential is printed to a terminal.

## Why it is not used here

A federation policy is created against the **account console URL**:

    ${ACCOUNT_CONSOLE_URL}/api/2.0/accounts/${ACCOUNT_ID}/servicePrincipals/${SP_ID}/federationPolicies

Databricks Free Edition documents **no access to the account console or
account-level APIs**. Probed rather than assumed: the endpoint returns
`Not Found` on this account, while the account SCIM endpoint — which Databricks
documents a separate workspace-domain route for — returns a valid response.
The Terraform and Pulumi resources agree from the other side; both state they
can only be used with an account-level provider.

So OIDC is unreachable on this tier. Not a preference, a capability.

## Decision

CI authenticates as a dedicated service principal using **OAuth M2M** with a
client secret held in a GitHub Actions secret.

    DATABRICKS_AUTH_TYPE:      oauth-m2m
    DATABRICKS_HOST:           <workspace url>     not secret
    DATABRICKS_CLIENT_ID:      <application id>    not secret, it is a username
    DATABRICKS_CLIENT_SECRET:  <secret>            GitHub Actions secret only

A **dedicated** service principal, `cscie103-olap-oltp-ci`, not the sibling
project's. Databricks guidance is one service principal per distinct external
workload: it preserves audit-log attribution and lets one workload be revoked
without affecting others. The catalog is this project's isolation boundary and
identity is the other half of it.

## Constraints this decision accepts

**The secret is long-lived where a federated token would not be.** Bounded at 90
days rather than unbounded, because a credential with no expiry is one nobody
ever rotates — an expiring one forces the rotation path to stay working.

**The secret must never render.** Databricks returns an OAuth client secret
exactly once, in the response body. The sibling project generated one with a
bare API call, it printed to the terminal, and it had to be revoked and reissued
— it was in scrollback, in shell history, and in every log capturing that
session. The remedy is not "be careful": the secret is piped from the API
straight into `gh secret set`, never through a variable, a file, or a screen.

**What is and is not secret.** The application id is the OAuth client ID and
belongs in the repository and in workflow files, exactly like a username. Only
the client secret is sensitive.

## MIGRATION TRIGGER

If this account moves to a paid tier, delete the secret and replace it with a
service principal federation policy. This decision exists because of a tier
limitation, not because a stored secret is the right answer in 2026.

The trigger is the tier change, not a date. A calendar review either fires when
nothing has changed or misses the change by months.

## Alternatives rejected

**A personal access token.** A long-lived bearer credential with broader scope
than an M2M client secret, and tied to a human identity — strictly worse on
every axis.

**Dropping the three gates from CI.** They would become local-only checks that
nobody runs, while the CI summary still reported sixteen gates. A gate that
appears to run and does not is worse than one that is absent.

**Reusing the sibling's service principal.** It would work immediately and
destroy the isolation this project is built around: shared audit attribution,
and no way to revoke one project without breaking the other.
