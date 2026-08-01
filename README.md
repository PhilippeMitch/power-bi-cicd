# Power BI / Fabric CI/CD (feature/release GitFlow → DEV/VAL/PROD)

This repo deploys Power BI / Fabric workspace items with
[fabric-cicd](https://microsoft.github.io/fabric-cicd/) through a
GitFlow-style branching model. Each target branch has two workflows: a
**CI** workflow that runs checks when a PR is *opened* against it, and a
**CD** workflow that runs the deployment only when that PR is actually
**merged**.

| Source branch | Target branch | Workspace | GitHub Environment | Approval required? |
|----------------|----------------|-----------|---------------------|---------------------|
| `feature/*`    | `dev`          | DEV       | `dev`               | No                  |
| `release/*`    | `val`          | VAL       | `val`               | Yes                 |
| `release/*`    | `main`         | PROD      | `prod`              | Yes                 |

A `feature/*` branch is opened off `dev` for day-to-day work. A
`release/*` branch is cut when a set of changes is ready to test, and is
PR'd into **both** `val` (for validation) and, once validated, `main`
(for production) — the same release branch, two separate PRs.

```
.
├── .github/workflows/
│   ├── ci-dev.yml         # PR checks: feature/* -> dev
│   ├── deploy-dev.yml     # deploys on merge:  feature/* -> dev
│   ├── ci-val.yml         # PR checks: release/* -> val
│   ├── deploy-val.yml     # deploys on merge:  release/* -> val
│   ├── ci-prod.yml        # PR checks: release/* -> main
│   └── deploy-prod.yml    # deploys on merge:  release/* -> main
├── scripts/
│   └── deploy.py          # fabric-cicd deployment script
├── workspace/              # exported Fabric items (Reports, SemanticModels, Notebooks, ...)
│   └── parameter.yml      # fabric-cicd environment-specific value swaps (must live here, not in a subfolder)
└── requirements.txt
```

---

## 1. Overall flow

1. Create a **`feature/<name>`** branch off `dev` for day-to-day work.
   Opening a PR from it into `dev` triggers `ci-dev.yml` (branch-naming
   check, `parameter.yml` validation, Python syntax check, secret scan).
   Merging that PR triggers `deploy-dev.yml`, which deploys straight to
   the **DEV** workspace — no approval required.
2. When a set of DEV changes is ready to test, cut a **`release/<name>`**
   branch (off `dev`) and open a PR **`release/<name>` → `val`**. Opening
   it triggers `ci-val.yml`; merging it triggers `deploy-val.yml`, which
   pauses for an approval (see step 4) and then deploys to the **VAL**
   workspace.
3. Once validated, open a PR from the **same `release/<name>` branch →
   `main`**. Opening it triggers `ci-prod.yml`; merging it triggers
   `deploy-prod.yml`, which again pauses for approval and then deploys to
   **PROD**.
4. Approvals are enforced by **GitHub Environment protection rules**, not
   by anything in the YAML — see section 3 below.
5. CD workflows trigger on the `pull_request` `closed` event and check
   `github.event.pull_request.merged == true` plus the source-branch
   prefix (`feature/`/`release/`) — a closed-but-not-merged PR (i.e. one
   that was just closed/declined) will not deploy anything.

---

## 2. One-time Azure / Fabric setup

### 2.1 Create a service principal (used by all three pipelines)

1. Azure Portal → **Microsoft Entra ID** → **App registrations** → **New registration**.
   Name it e.g. `sp-fabric-cicd`.
2. Note down:
   - **Application (client) ID**
   - **Directory (tenant) ID**
3. Go to **Certificates & secrets** → **New client secret**. Copy the
   secret **value** immediately (it's only shown once).

You can reuse the same service principal for DEV/VAL/PROD, or create a
separate one per environment if you want tighter blast-radius control.
Everything below assumes one shared SP for simplicity.

### 2.2 Allow the service principal to call Fabric APIs

1. [Power BI Admin portal](https://app.powerbi.com/admin-portal) →
   **Tenant settings** → **Developer settings**.
2. Enable **"Service principals can use Fabric APIs"** (and **"Service
   principals can call Fabric public APIs"** if shown separately).
3. Scope it to a security group, and add the app registration's
   associated service principal / security group to that group.

### 2.3 Give the service principal access to each workspace

For **each** of the DEV, VAL and PROD workspaces:

1. Open the workspace → **Manage access** → **Add people or groups**.
2. Add the service principal (search by the app name) with the
   **Member** or **Contributor** role (Member is enough for
   `fabric-cicd` to publish items).

### 2.4 Get the three workspace IDs

In each workspace, open **Workspace settings** (or look at the URL:
`.../groups/<workspace-id>/...`) and copy the GUID for DEV, VAL and PROD.

---

## 3. GitHub repository configuration

### 3.1 Branches

Create the three long-lived branches (if not already present):

```bash
git checkout -b dev
git checkout -b val
git checkout -b main
```

`feature/*` and `release/*` are short-lived branches created per change —
`feature/*` off `dev`, `release/*` also off `dev` (then PR'd into both
`val` and `main`).

Recommended **branch protection rules** (Settings → Branches → Add rule)
for `dev`, `val` and `main`:
- Require a pull request before merging.
- Require status checks to pass — select the matching CI workflow
  (`checks` job from `ci-dev.yml` / `ci-val.yml` / `ci-prod.yml`) so a PR
  can't merge until the branch-naming check, `parameter.yml` validation,
  and secret scan pass.
- Do not allow direct pushes.

### 3.2 Create the GitHub Environments

Go to **Settings → Environments → New environment** and create three:
`dev`, `val`, `prod`.

These names must exactly match the `environment:` key used in each
workflow file (`deploy-dev.yml` → `dev`, etc.), because that's how GitHub
decides which secrets and which protection rules apply to each job.

### 3.3 Configure required reviewers (the approval step you asked for)

For the **`val`** and **`prod`** environments only:

1. Open the environment → **Deployment protection rules** → check
   **Required reviewers**.
2. Add the person/team who must approve deployments (e.g. a BI lead for
   VAL, and a different/stricter group for PROD).
3. Optionally set **"Prevent self-review"** so the person who merged the
   PR can't also approve the deployment.

Leave **Required reviewers unchecked** on the `dev` environment so DEV
deploys automatically.

### 3.4 (Recommended) Restrict each environment to its own branch

Still inside each environment's settings, under **Deployment branches
and tags**, choose **"Selected branches"** and add only:
- `dev` → environment `dev`
- `val` → environment `val`
- `main` → environment `prod`

This is a safety net: even if a workflow file is accidentally triggered
from the wrong branch, GitHub will refuse to run it against an
environment that doesn't allow that branch.

### 3.5 Add secrets — at the Environment level, not the repo level

For each environment (`dev`, `val`, `prod`), go to that environment's
**Environment secrets** section and add:

| Secret name | Value |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID from step 2.1 |
| `AZURE_CLIENT_ID` | Application (client) ID from step 2.1 |
| `AZURE_CLIENT_SECRET` | Client secret value from step 2.1 |
| `FABRIC_WORKSPACE_ID` | The DEV/VAL/PROD workspace GUID (different per environment) from step 2.4 |

Because each workflow's job declares `environment: name: dev|val|prod`,
`${{ secrets.X }}` automatically resolves to that environment's copy of
the secret — so `FABRIC_WORKSPACE_ID` correctly points at a different
workspace in each pipeline even though the secret name is identical.

If you created one SP per environment instead of a shared one, just put
the matching `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` in each environment.

---

## 4. How a deployment actually runs

1. A PR from `feature/*` → `dev`, `release/*` → `val`, or `release/*` →
   `main` gets **merged** (not just closed).
2. The matching CD workflow (`deploy-dev.yml` / `deploy-val.yml` /
   `deploy-prod.yml`) fires on the `pull_request` `closed` event, checks
   `merged == true` and the source-branch prefix, and — if both match —
   starts a job bound to the corresponding environment.
3. For `val` and `prod`, the job shows as **"Waiting"** in the Actions
   tab until a required reviewer approves it from the run's page.
4. Once unblocked (or immediately, for `dev`), the job:
   - checks out the repo,
   - installs `fabric-cicd` and `azure-identity`,
   - runs `python scripts/deploy.py --workspace-id ... --environment DEV|VAL|PROD --repo-dir workspace --unpublish-orphans`.
5. `scripts/deploy.py` authenticates as the service principal
   (`ClientSecretCredential`), builds a `FabricWorkspace`, and calls
   `publish_all_items` (and `unpublish_all_orphan_items` to remove items
   deleted from the repo).

---

## 5. Local testing

```bash
pip install -r requirements.txt

export AZURE_TENANT_ID="..."
export AZURE_CLIENT_ID="..."
export AZURE_CLIENT_SECRET="..."

python scripts/deploy.py \
  --workspace-id "<dev-workspace-id>" \
  --environment DEV \
  --repo-dir workspace
```

---

## 6. Pointing each environment at the right Azure SQL database

Both tables (`Budget` and `Actuals`) read from an **Azure SQL Database**
via `Sql.Databases(...)`, authored locally in Power BI Desktop using its
native SQL Server connector with **SQL Login** (username/password)
authentication. Because this is a cloud-hosted Azure SQL Database (not
an on-prem SQL Server), **no gateway is required** — the Fabric service
connects directly, the same way Power BI Desktop does.

The three environments share the **same logical server**
(`sales-pbi-db.database.windows.net`) but each has its **own database**
— only the database name differs per environment.

### 6.1 M query, and what `parameter.yml` swaps

Each table's Power Query step (see
`workspace/sales.SemanticModel/definition/tables/Actuals.tmdl` and
`Budget.tmdl`) uses:

```
let
    Source = Sql.Databases("sales-pbi-db.database.windows.net"),
    #"free-sql-db-5932339" = Source{[Name="free-sql-db-5932339"]}[Data],
    dbo_Budget = #"free-sql-db-5932339"{[Schema="dbo",Item="Budget"]}[Data]
in
    dbo_Budget
```

The database name `free-sql-db-5932339` appears twice — once as the
step name, once as the `Name=` literal — and `workspace/parameter.yml`
swaps both occurrences together with a single `find_replace` entry per
table (`find_value` must match the DEV string **character for
character**, or the swap silently won't apply). VAL/PROD currently hold
placeholder database names (`free-sql-db-VAL` / `free-sql-db-PROD`) —
update those in `parameter.yml` once the real VAL/PROD databases exist.

This file **must** be named `parameter.yml` and live at the root of
`workspace/` (the folder passed as `--repo-dir`) — `fabric-cicd` looks
for it there automatically, no extra flag needed in `scripts/deploy.py`.

### 6.2 SQL Login credentials via centrally-managed Fabric Connections

SQL Login credentials are **never** committed to source control — they
aren't part of `parameter.yml`, the `.tmdl` files, or anywhere else in
`workspace/`. Instead of setting per-workspace data source credentials
by hand, this repo uses a **Fabric Connection** per environment (a
reusable, centrally-managed resource that holds the SQL Login) and
binds the semantic model to it automatically on every deploy via the
`semantic_model_binding` block in `parameter.yml`.

**One-time setup, per environment (DEV/VAL/PROD):**

1. Go to the [Fabric/Power BI admin portal](https://app.powerbi.com) →
   **Manage connections and gateways** → **New** → **Cloud connection**.
2. Connection type: **SQL Server** (or **Azure SQL Database**). Server:
   `sales-pbi-db.database.windows.net`. Database: that environment's
   database name (the same value used as the `VAL`/`PROD`
   `replace_value` in `parameter.yml`'s `find_replace` section above).
3. Authentication method: **Basic** (SQL Login) — enter the SQL Login
   username/password for that environment's database.
4. Under **Who can use this connection**, share it with (at least) the
   service principal used by the CD pipeline (see section 2.1) so
   `fabric-cicd` is allowed to bind the semantic model to it.
5. Get that connection's **ID** (a GUID) — the "Manage connections and
   gateways" list view doesn't display it and doesn't change the URL
   when you click a row, so use one of:
   - **Browser DevTools**: with the connections list page open, press
     `F12` → **Network** tab → filter **Fetch/XHR** → reload the page.
     Find the request that returns the connections list as JSON, open
     its **Response**/**Preview**, and read the `id` (or `objectId`)
     next to the matching connection name.
   - **REST API** (scriptable, most reliable): call
     [List Connections](https://learn.microsoft.com/en-us/rest/api/fabric/core/connections/list-connections):
     ```powershell
     $token = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
     Invoke-RestMethod -Uri "https://api.fabric.microsoft.com/v1/connections" -Headers @{ Authorization = "Bearer $token" }
     ```
     (requires `az login` first, with the same account shown as the
     connection's owner in the portal — e.g. "Jean" in the list view).
     The response's `value[]` array has one object per connection with
     its `displayName` and `id` — match on `displayName` (e.g.
     `sales-pbi-db-connections`, `free-sql-db-val-connections`,
     `sales-pbi-db-prod-connections`) and copy the corresponding `id`.
6. Paste that GUID into `workspace/parameter.yml`, under
   `semantic_model_binding.models[].connection_id`, for the matching
   environment key (`DEV`/`VAL`/`PROD`).

Once all three GUIDs are real (not the `00000000-...` placeholders),
every deploy calls `bindConnection` for the target environment, so the
model is fully authenticated with no manual per-workspace step. If a
database's credentials are rotated, update them **on the Connection
itself** (not in this repo) — the binding doesn't need to change unless
the Connection ID or the server/database changes.

**Two prerequisites `bindConnection` silently depends on:**

- **The Connection must be shared with the service principal.** Open
  the Connection (Admin portal → Manage connections and gateways) →
  **Manage users** → add the SP (search by app name, or by its
  Application/client ID GUID if the name doesn't resolve) with **User**
  access. Without this, `bindConnection` fails with a 403 that
  fabric-cicd only logs as a `warning`, not a hard error — the deploy
  still reports success.
- **The service principal must own the semantic model.** Fabric's
  `bindConnection` API requires the caller to be the model's owner. A
  model first published by a human (e.g. via Fabric Git sync) keeps
  that human as owner even after the SP republishes its content, so
  binding fails with *"you cannot configure the data connection
  bindings because you are not the owner"*. This is a known,
  still-open gap in fabric-cicd itself
  ([microsoft/fabric-cicd#824](https://github.com/microsoft/fabric-cicd/issues/824))
  — it never takes ownership before binding. `scripts/deploy.py`
  works around it: before calling `publish_all_items`, it takes
  ownership (via the classic Power BI `Default.TakeOver` API) of every
  semantic model in `--repo-dir` that already exists in the target
  workspace. This runs on every deploy and is idempotent.

Locally, Power BI Desktop still prompts for the same SQL Login the
first time you open/refresh the `.pbip` against a given database —
that's independent of the Fabric Connection described here.

---

## 7. Notes / next steps

- `--item-types` in `scripts/deploy.py` defaults to `Notebook`,
  `DataPipeline`, `Environment`, `SemanticModel`, `Report`. Trim or
  extend this list to match what you actually keep in `workspace/`.
- Nothing is committed to `workspace/` yet — populate it either by
  connecting the DEV workspace to this repo via Fabric's Git integration
  (recommended) or by exporting PBIP-format items manually.
- See the [fabric-cicd parameterization docs](https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/)
  for additional capabilities beyond `find_replace` (e.g.
  `key_value_replace` for JSON/YAML keys, `spark_pool` mappings, and
  `semantic_model_binding` if you later manage the Azure SQL connection
  via a centrally-defined Fabric "Connection" instead of per-workspace
  data source credentials).
