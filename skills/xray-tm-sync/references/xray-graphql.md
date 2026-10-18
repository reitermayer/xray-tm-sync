# Xray Cloud API used by the skill

## Why GraphQL

Xray Cloud's REST API has no endpoint that returns manual test step definitions. GraphQL is the only
API that returns, in one paged query: steps, repository folder, preconditions, test type, and the Jira
fields (`jira(fields: [...])`), so no separate Atlassian credential is needed.

## Auth (no OAuth available)

```
POST https://<region.>xray.cloud.getxray.app/api/v2/authenticate
{"client_id": "...", "client_secret": "..."}
→ 200 "eyJ…"   (JSON string, a JWT valid 24 h)
```
Then `Authorization: Bearer <jwt>` on `/api/v2/graphql`. Hosts: `xray.cloud.getxray.app` (global),
`eu.`, `us.`, `au.` (`XRAY_REGION`). The API key belongs to a Jira user; results follow that user's
Jira permissions. A 401 triggers one re-authentication.

## Query

```graphql
query($jql: String, $since: String, $limit: Int!, $start: Int) {
  page: getExpandedTests(jql: $jql, modifiedSince: $since, limit: $limit, start: $start) {
    total start limit
    results {
      issueId projectId lastModified
      testType { name kind }
      folder { path }
      gherkin unstructured
      preconditions(limit: 10) {
        total
        results { issueId definition preconditionType { name kind } jira(fields: ["key", "summary"]) }
      }
      testSets(limit: 20) { total results { issueId jira(fields: ["key", "summary"]) } }
      jira(fields: ["key", "summary", "description", "labels", "status", "updated"])
      steps { id action data result calledTestIssueId }
      warnings
    }
  }
}
```
`pull --no-expand` uses `getTests` (no called-test inlining) if `getExpandedTests` misbehaves.

## Limits

- `limit` ≤ 100 per connection; `xr` pages with `start` until `total`.
- ≤ 10 000 items per query, nested connections multiply: 100 tests × (10 preconditions + 20 Xray test sets) = 3 000.
- ≤ 25 resolvers per query.
- Rate: 300 requests / 5 min (Standard), 1 000 (Enterprise). HTTP 429 → wait `Retry-After`, back off.
- If the JQL error says it matched too many issues, split the JQL (key range, folder, date window).

## Incremental pulls

`modifiedSince` ("all tests modified after this date") covers Xray-side changes such as steps.
JQL `updated >=` covers only Jira-side changes, so prefer `--since`.

Schema reference: https://us.xray.cloud.getxray.app/doc/graphql/

## Results import (write; `report --yes` only)

`POST /api/v2/import/execution` with an Xray JSON body (`info` + `tests[]` with `testKey`, `status`,
optional `steps[]`) → `{"id", "key", "self"}`. Creates one Xray test execution in `info.project`.
Needs an API key whose Jira user may create Xray test execution issues. Details and the mock guard:
[execution-and-results.md](execution-and-results.md).
