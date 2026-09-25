# Deployment guide

Two paths: **Part A** runs the whole platform on your own machine with Docker Compose (5–10
minutes, free). **Part B** hosts it for real on AWS using the Terraform in
`infrastructure/terraform/` (~45–60 minutes the first time, real AWS cost — see the estimate in
that section before you start). **Part C** notes what changes on another cloud.

Every command below is written to be copy-pasted as-is from `platform/` unless a step says
otherwise.

---

## Part A — Run it locally

### A1. Install prerequisites

1. **Docker Desktop** — download from https://www.docker.com/products/docker-desktop/ and install
   it for your OS. Open it once and confirm it says "Docker Desktop is running" (whale icon in
   your menu bar/system tray). Everything else in this platform runs *inside* Docker, so this is
   the only heavyweight install you need for Part A.
2. **Git** — `git --version` in a terminal; if that fails, install from https://git-scm.com/downloads.
3. (Optional, only needed if you want to run the Python test suite outside Docker) **Python 3.12+**.

### A2. Get the code

```bash
git clone <your-fork-or-repo-url>
cd Hackathon_ET_Accenture/platform
```

### A3. Get an OpenRouter API key (optional but recommended)

The platform defaults to a **mock** model gateway with zero setup — every agent still runs and
returns schema-valid responses, so you can see the whole pipeline work with no API key at all. For
*real* LLM reasoning:

1. Open https://openrouter.ai in your browser and sign up (or sign in).
2. Click your account menu (top right) → **Keys** (or go directly to
   https://openrouter.ai/settings/keys).
3. Click **Create Key**, give it a name (e.g. `reo-local`), and copy the value — it starts with
   `sk-or-v1-`. You will not be able to see it again after leaving the page.
4. Add a small amount of credit on the **Credits** page if your account is new (OpenRouter is
   pay-as-you-go; `openai/gpt-4o-mini`, the platform's default model, costs fractions of a cent per
   agent call).

### A4. Configure environment variables

`docker compose` reads a `.env` file from the same folder as `docker-compose.yml`
(`infrastructure/`). Easiest: run the helper script, which creates it from `.env.example` if
needed and prompts for the key with hidden input (never echoed, never in shell history):

```bash
./infrastructure/set_api_key.sh OPENROUTER_API_KEY
```

It also offers to set `MODEL_PROVIDER=openrouter` to match. Re-run it any time to rotate the key
or switch provider (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are supported the same way).

Or by hand: `cp .env.example infrastructure/.env`, then open `infrastructure/.env` in your editor
and fill in:

```bash
MODEL_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...          # from step A3; leave MODEL_PROVIDER=mock and skip this to run with no key
OPENROUTER_MODEL=openai/gpt-4o-mini
```

`infrastructure/.env` is in `.gitignore` — it will never be committed. Never paste a real API key
into any file that isn't gitignored.

### A5. Bring the stack up

```bash
cd infrastructure
docker compose up -d --build
```

First run pulls base images and builds 8 containers — expect 3–8 minutes depending on your
connection. Watch progress with:

```bash
docker compose ps
```

Wait until every row shows `Up` (and `healthy` for `postgres`/`redis`/`minio`). Then check the API
directly:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### A6. Seed the reference tenant

This creates the demo utility's portfolio (5 solar farms, 3 wind farms, 2 battery units, 6
consumer load groups, one grid interconnection) and one login per role:

```bash
docker compose run --rm api python /app/platform/database/seed.py
```

You'll see a line ending `demo login: any email above / password 'Password123!' / tenant_slug
'demo-utility'`.

### A7. Open the dashboard

1. Open your browser to **http://localhost:5173**.
2. Log in with:
   - Tenant slug: `demo-utility`
   - Email: any seeded email from the table below
   - Password: `Password123!`
3. You should land on **Portfolio Operations** showing an **idle** state — a fresh tenant doesn't
   start making decisions on its own. Connect a data source first, then start it:
   - Either click **Document Intake** (top of Portfolio Operations) and upload a file — any
     `.csv`/`.json`/`.xlsx` (structured telemetry) or `.pdf`/`.png`/`.jpg` (a document, read via
     vision). A successful upload **auto-starts** the optimizer, no extra click needed; or
   - Go to **Connector Studio**, register a connector (any `kind`), have a *different* user
     activate it (maker-checker — e.g. `tenant.admin` creates, `platform.admin` activates), then
     back on Portfolio Operations click **Start Optimizer** (enabled once at least one connector
     is active or a document has been ingested).
4. Once started, Portfolio Operations fills in with live generation/demand numbers that update
   every few seconds (the built-in `edge-simulator` publishes synthetic telemetry continuously
   regardless of this gate — starting/stopping controls the decision cycle and dashboard display,
   not the underlying telemetry stream), and the sidebar's 14 workspaces come alive — Decision
   Centre is a good second stop; it fills in with a new entry roughly every 2 minutes as the
   optimizer's decision cycle runs. **Stop Optimizer** on Portfolio Operations returns to idle at
   any time.

#### Demo accounts (one per role)

All 9 seeded accounts share the same tenant slug and password — these are local/demo-only
credentials from `database/seed.py`, never used outside seed data. Pick whichever role you want to
test; the dashboard's RBAC gates what each one can see and do (e.g. only Operator/Senior Operator
can decide items in Approval Inbox — everyone else sees it read-only).

**Tenant slug:** `demo-utility` · **Password (all accounts):** `Password123!`

| Role | Email | Can do |
|---|---|---|
| Viewer | `viewer@demo-utility.test` | Read-only dashboard/decisions/audit |
| Operator | `operator@demo-utility.test` | + Approve assigned items, acknowledge signals, bounded override |
| Senior Operator | `senior.operator@demo-utility.test` | + Four-eyes approval, pause ops, e-stop |
| Portfolio Manager | `portfolio.manager@demo-utility.test` | Objective policy, scenarios, constraints, file ingestion, economics |
| OT Admin | `ot.admin@demo-utility.test` | Adapters, command envelopes, control readiness |
| Model Admin | `model.admin@demo-utility.test` | Model registry, eval runs, model deploys |
| Tenant Admin | `tenant.admin@demo-utility.test` | Users, settings, connectors, policies, file ingestion |
| Auditor / DPO | `auditor@demo-utility.test` | Evidence export, privacy, DSR |
| Platform Admin | `platform.admin@demo-utility.test` | Everything above + tenant provisioning, platform config, break-glass |

Other useful local URLs:
- API interactive docs: http://localhost:8000/docs
- OT gateway simulator health: http://localhost:8010/health
- MinIO console (object storage): http://localhost:9001 — login `reo-minio` / `reo-minio-secret`

### A8. Run the test suite and the live demo script

```bash
cd ..   # back to platform/
python3 -m venv .venv && source .venv/bin/activate
pip install -e packages/reo_common
pip install -r backend/requirements.txt -r policy/requirements.txt -r agent/requirements.txt
pip install pytest

DATABASE_URL=postgresql+psycopg2://reo:reo@127.0.0.1:5433/reo \
REDIS_URL=redis://127.0.0.1:6380/0 \
pytest tests -v
```

Then, with the stack still running, drive the live 10-step demo script (doc 08 §4) end-to-end:

```bash
python3 tests/demo_runner.py --base-url http://localhost:8000
```

### A9. (optional) Ingest the reference dataset

If you have the reference dataset (`Renewable_Energy_Orchestrator_Dataset`, ~100MB of CSVs) in
`../data/` — see `docs/DATA_INGESTION.md` for exactly what it contains — load it:

```bash
docker compose exec api python3 -m app.ingestion.hackathon_dataset
```

This does two things: loads the four portfolio-level files as real telemetry (solar/wind output,
grid frequency, market price, weather — a few seconds), and ingests the 100 retail/SME/industrial
customer accounts (~10-15s, since it aggregates 863,600 15-minute readings down to 9,000 daily
rows on the way in). The customer data then shows up in the dashboard's **Customers** workspace
(Customer Insights tab), filterable by type/region/customer ID.

### A10. Stopping / cleaning up

```bash
cd infrastructure
docker compose down          # stop everything, keep the database volume
docker compose down -v       # stop everything AND delete all data (fresh slate next time)
```

### Troubleshooting (local)

| Symptom | Fix |
|---|---|
| `docker compose up` fails with a port-already-in-use error on 5432/6379 | You likely have a native Postgres/Redis running. This platform already maps around that (host ports `5433`/`6380`) — check nothing *else* is also on those two, or edit the `ports:` lines in `infrastructure/docker-compose.yml`. |
| `api` container keeps restarting | `docker compose logs api --tail 50` — almost always either the `migrate` service hasn't finished (check `docker compose logs migrate`) or `infrastructure/.env` has a typo. |
| Dashboard loads but shows no data | Two possible causes: (1) Did you run step A6 (seed)? `docker compose run --rm api python /app/platform/database/seed.py` is safe to re-run — it no-ops if the tenant already exists. (2) Portfolio Operations shows an **idle** empty state by design until you connect a data source and click Start Optimizer (see A7 step 3) — this isn't a bug. |
| Agents seem to give generic/templated answers | You're on the mock gateway — check `MODEL_PROVIDER=openrouter` and a real `OPENROUTER_API_KEY` are set in `infrastructure/.env`, then `docker compose up -d --build api agent-worker` to pick up the change. |
| `pip install` fails on `psycopg2-binary` (Apple Silicon) | Install PostgreSQL client libs first: `brew install postgresql`, then retry. |
| Just did a full database wipe (`TRUNCATE ... CASCADE` or similar) and want a genuinely clean state | Reseed (`database/seed.py`) — that's it. `edge-simulator` re-checks the current tenant every ~10s tick and reloads its portfolio automatically when it changes (a fresh `Tenant` row means a new id); `api`/`optimizer-worker`/`agent-worker` already re-resolve the current tenant on every request/cycle, so nothing needs a manual restart. |

---

## Part B — Deploy to AWS

### B0. What this creates, and what it costs

`infrastructure/terraform/` provisions, in one AWS account/region (default `eu-west-1`):

- A VPC across 3 Availability Zones with public / private-app / private-data / isolated-OT-DMZ
  subnet tiers, 3 NAT gateways
- ECS Fargate running all 6 backend services (`api`, `optimizer-worker`, `agent-worker`,
  `ot-gateway-sim`, `edge-simulator`, `export-worker`), each in its own ECR repository
- RDS PostgreSQL (Multi-AZ) and ElastiCache Redis (2 nodes, automatic failover)
- An Application Load Balancer in front of the API
- An S3 bucket + CloudFront distribution serving the built React dashboard
- KMS-encrypted Secrets Manager entries for every credential (DB URL, Redis URL, JWT secret,
  OpenRouter/Anthropic API keys)

**This is real infrastructure with a real ongoing bill.** At the Terraform's documented defaults
(`db.r6g.large`, `cache.r6g.large`, Multi-AZ RDS, 3 NAT gateways) this is roughly
**$450–650/month** — appropriate for a genuine staging/production environment, not for "let me
just look at it in AWS." For a cheap evaluation deployment, override the instance sizes at apply
time (shown in B6) to bring it down to roughly **$120–180/month**; the dominant remaining costs at
that size are the 3 NAT gateways (~$32/month each) and Multi-AZ RDS. **Always run
`terraform destroy` (step B15) when you're done evaluating** — nothing here has a free tier.

### B1. Open the AWS console and create an account (skip if you already have one)

1. Go to https://aws.amazon.com/ and click **Create an AWS Account** (top right).
2. Follow the signup flow: email, password, AWS account name, contact information, and a payment
   card (AWS requires one even if you stay within any free tier — this deployment will exceed free
   tier limits, per B0 above).
3. Choose the **Basic support plan** (free) unless you specifically want paid support.
4. Once signed up, you land on the **AWS Management Console** at https://console.aws.amazon.com/.
   Note the account ID shown under your account name (top right) — you'll need it later.

### B2. Create an IAM user for Terraform (don't use your root account)

Using the AWS account's root login for day-to-day work (including running Terraform) is against
AWS's own guidance. Create a dedicated user:

1. In the console's top search bar, type **IAM** and click the **IAM** service.
2. In the left sidebar, click **Users**, then click **Create user** (top right).
3. User name: `terraform-deploy`. Do **not** check "Provide user access to the AWS Management
   Console" (this user only needs API access). Click **Next**.
4. On the permissions step, choose **Attach policies directly**. For a first deployment, search
   for and check **AdministratorAccess** (this Terraform creates VPCs, IAM roles, RDS, ECS, KMS,
   CloudFront and more — scoping a minimal policy down is a good follow-up once you've deployed
   successfully once, not a first-run task). Click **Next**, then **Create user**.
5. Click into the new `terraform-deploy` user, go to the **Security credentials** tab, scroll to
   **Access keys**, and click **Create access key**.
6. Choose **Command Line Interface (CLI)**, acknowledge the recommendation, click **Next**, then
   **Create access key**.
7. Copy the **Access key ID** and **Secret access key** shown (or click **Download .csv file**) —
   the secret is shown only once.

### B3. Install and configure the AWS CLI and Terraform locally

1. Install the AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
   (macOS: `brew install awscli`).
2. Install Terraform (>= 1.7): https://developer.hashicorp.com/terraform/install
   (macOS: `brew install terraform`).
3. Configure your credentials from step B2:

   ```bash
   aws configure
   # AWS Access Key ID: <paste from B2>
   # AWS Secret Access Key: <paste from B2>
   # Default region name: eu-west-1
   # Default output format: json
   ```
4. Verify:

   ```bash
   aws sts get-caller-identity
   ```

   should print your new user's account ID and ARN.

### B4. Set up remote Terraform state (recommended)

By default this Terraform stores state as a local file, which is fine solo but risky the moment
more than one person (or CI) touches it. To use the S3 backend already stubbed in
`infrastructure/terraform/providers.tf`:

1. In the console, go to **S3** → **Create bucket**. Name it something globally unique like
   `reo-terraform-state-<your-account-id>` (use the account ID from B1). Region: `eu-west-1`.
   Leave "Block all public access" checked. Enable **Bucket Versioning** under the bucket's
   **Properties** tab after creation. Click **Create bucket**.
2. Go to **DynamoDB** → **Create table**. Table name: `reo-terraform-locks`. Partition key:
   `LockID` (type String). Leave everything else default. Click **Create table**.
3. Open `infrastructure/terraform/providers.tf`, uncomment the `backend "s3" { ... }` block, and
   fill in the bucket name from step 1.
4. You'll run `terraform init` in B6 below, which picks this up.

(Skip this section and stay on local state if you're just doing a one-off evaluation deploy — B6's
`terraform init` works either way.)

### B5. Get an OpenRouter API key

Same as step A3 above if you haven't already: https://openrouter.ai/settings/keys.

### B6. Initialize and apply the Terraform

Every command from here through B10 assumes your terminal is at the **repository root** — the
folder containing `platform/` (if you're continuing on from Part A, that's `cd ../..` from
`platform/infrastructure`, or just open a fresh terminal and `cd` to wherever you cloned it in A2).

```bash
cd platform/infrastructure/terraform
terraform init
```

Review the plan before applying anything — **always read what Terraform intends to create**:

```bash
terraform plan \
  -var="openrouter_api_key=sk-or-v1-..." \
  -out=tfplan
```

For a cheaper evaluation deployment, also override the instance sizes (see B0's cost note):

```bash
terraform plan \
  -var="openrouter_api_key=sk-or-v1-..." \
  -var="db_instance_class=db.t4g.medium" \
  -var="redis_node_type=cache.t4g.small" \
  -out=tfplan
```

Read through the plan output — it should list ~60–70 resources to add, 0 to change, 0 to destroy.
Then apply:

```bash
terraform apply tfplan
```

This takes **15–25 minutes** (RDS and the NAT gateways are the slow parts). When it finishes,
capture the outputs:

```bash
terraform output
terraform output -raw api_base_url          # the ALB URL the frontend and you will use
terraform output -raw ecs_cluster_name
terraform output ecr_repository_urls
```

### B7. Build and push the container images

Each of the 6 backend services needs its image built and pushed to the ECR repository Terraform
just created for it. From `platform/`:

```bash
cd ../..   # back to platform/
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=eu-west-1
REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $REGISTRY
```

Then build and push each of the 6 (this is deliberately six separate copy-pasteable commands per
service, not a loop over an associative array — those need bash 4+, and macOS ships bash 3.2 by
default):

```bash
docker build --platform linux/amd64 -f backend/Dockerfile -t "$REGISTRY/reo-api:latest" .. && docker push "$REGISTRY/reo-api:latest"
docker build --platform linux/amd64 -f policy/Dockerfile -t "$REGISTRY/reo-optimizer-worker:latest" .. && docker push "$REGISTRY/reo-optimizer-worker:latest"
docker build --platform linux/amd64 -f agent/Dockerfile -t "$REGISTRY/reo-agent-worker:latest" .. && docker push "$REGISTRY/reo-agent-worker:latest"
docker build --platform linux/amd64 -f guardrails/ot-gateway-sim/Dockerfile -t "$REGISTRY/reo-ot-gateway-sim:latest" .. && docker push "$REGISTRY/reo-ot-gateway-sim:latest"
docker build --platform linux/amd64 -f infrastructure/edge-simulator/Dockerfile -t "$REGISTRY/reo-edge-simulator:latest" .. && docker push "$REGISTRY/reo-edge-simulator:latest"
docker build --platform linux/amd64 -f output/export-worker/Dockerfile -t "$REGISTRY/reo-export-worker:latest" .. && docker push "$REGISTRY/reo-export-worker:latest"
```

(`--platform linux/amd64`: Fargate runs x86_64 by default; include this even if you're building on
an Apple Silicon Mac, or add `runtime_platform` to the task definitions for `ARM64` instead.)

### B8. Redeploy the ECS services to pick up the images

The ECS services were created in B6 pointing at the `:latest` tag, but they started before any
image existed at that tag. Force a new deployment now that images are pushed:

```bash
CLUSTER=$(terraform -chdir=infrastructure/terraform output -raw ecs_cluster_name)
for name in api optimizer-worker agent-worker ot-gateway-sim edge-simulator export-worker; do
  aws ecs update-service --cluster "$CLUSTER" --service "reo-$name" --force-new-deployment --region eu-west-1
done
```

Watch a service come up (repeat for others, or check the console: **ECS** → your cluster →
**Services**):

```bash
aws ecs describe-services --cluster "$CLUSTER" --services reo-api --region eu-west-1 \
  --query 'services[0].deployments'
```

### B9. Run database migrations

There's no standing "migrate" service on AWS (it's a one-off, unlike the always-on services) — run
it as a one-off ECS task using the `api` task definition with its command overridden:

1. Get the network config the other tasks use (console: **ECS** → cluster → **Configuration** →
   note the private subnet IDs and the app security group; or):

   ```bash
   SUBNETS=$(aws ec2 describe-subnets --filters "Name=tag:Tier,Values=private-app" \
     --query 'Subnets[].SubnetId' --output text --region eu-west-1 | tr '\t' ',')
   SG=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=reo-app-*" \
     --query 'SecurityGroups[0].GroupId' --output text --region eu-west-1)
   ```

2. Run the migration task:

   ```bash
   aws ecs run-task \
     --cluster "$CLUSTER" \
     --task-definition reo-api \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
     --overrides '{"containerOverrides":[{"name":"api","command":["sh","-c","cd /app/platform/database && alembic upgrade head"]}]}' \
     --region eu-west-1
   ```

   (`ecs run-task --overrides` has no `workingDirectory` field, unlike `docker-compose.yml`'s
   `working_dir:` — the `cd &&` does the same job so `alembic` finds `alembic.ini` and resolves its
   relative `script_location` correctly, exactly as the local `migrate` service does.)

3. Check it succeeded (console: **ECS** → cluster → **Tasks** → find the task → **Logs** tab; or
   `aws logs tail /ecs/reo-api --since 5m --region eu-west-1`) — look for
   `Running upgrade ... -> 0008`, the final migration.

### B10. Seed the tenant, the same way, as a one-off task

```bash
aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition reo-api \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","/app/platform/database/seed.py"]}]}' \
  --region eu-west-1
```

### B11. Build and deploy the frontend

The dashboard is a static build (Vite bakes the API URL into the JS at build time), so it's built
locally/in CI and synced to the S3 bucket Terraform created, served through CloudFront:

```bash
API_URL=$(terraform -chdir=infrastructure/terraform output -raw api_base_url)
BUCKET=$(terraform -chdir=infrastructure/terraform output -raw frontend_bucket_name)

cd frontend
npm ci
VITE_API_BASE_URL="$API_URL" npm run build

aws s3 sync dist/ "s3://$BUCKET/" --delete --region eu-west-1

# CloudFront caches aggressively — invalidate so the new build is served immediately
DIST_ID=$(aws cloudfront list-distributions --region eu-west-1 \
  --query "DistributionList.Items[?Origins.Items[0].DomainName=='$BUCKET.s3.eu-west-1.amazonaws.com'].Id" --output text)
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" --region eu-west-1
cd ..
```

### B12. Open the deployed platform

```bash
terraform -chdir=infrastructure/terraform output -raw frontend_url
```

1. Open that URL (`https://<something>.cloudfront.net`) in your browser.
2. Log in exactly as in A7 (`demo-utility` / any seeded email / `Password123!`) — **change this
   password or reconfigure auth before giving anyone else access; it's a local/demo default, not
   meant to survive contact with the public internet.**
3. The API itself is reachable directly at `terraform output -raw api_base_url` (interactive docs
   at `<that-url>/docs`) if you want to check it independently of the dashboard.

Sanity-check the whole thing end to end the same way you would locally (still from `platform/` —
see B6's note):

```bash
python3 tests/demo_runner.py --base-url "$(terraform -chdir=infrastructure/terraform output -raw api_base_url)"
```

### B13. (optional) Put a real domain in front of it

By default the ALB serves plain HTTP on its AWS-generated DNS name (`B0`'s `certificate_arn`
variable is empty) and CloudFront serves on its default `*.cloudfront.net` domain. For a real
production domain:

1. **Route53** (console) → **Hosted zones** → **Create hosted zone** for your domain (or use one
   you already manage elsewhere — you'll just need to add records there instead).
2. **Certificate Manager (ACM)** (console, same region as the ALB, e.g. `eu-west-1`) → **Request a
   certificate** → **Request a public certificate** → enter your API subdomain (e.g.
   `api.yourcompany.com`) → **DNS validation** → **Request**. Click into the new certificate and
   **Create records in Route 53** to validate it (a few minutes to propagate).
3. Re-apply Terraform with the cert ARN (from `platform/`, per B6's note — or add `-chdir=infrastructure/terraform`):

   ```bash
   terraform -chdir=infrastructure/terraform apply \
     -var="openrouter_api_key=sk-or-v1-..." \
     -var="certificate_arn=arn:aws:acm:eu-west-1:...:certificate/..."
   ```

   This switches the ALB to HTTPS-only with an HTTP→HTTPS redirect (see `ecs.tf`'s listener
   resources).
4. In Route53, add an **A record (Alias)** for `api.yourcompany.com` pointing at the ALB.
5. For the dashboard on a custom domain too: request a *second* ACM certificate **in `us-east-1`**
   (CloudFront requires this specific region regardless of where everything else lives), add it and
   an alias to `frontend.tf`'s `aws_cloudfront_distribution` (`aliases` + `viewer_certificate`), and
   add a Route53 **A record (Alias)** for your chosen dashboard hostname pointing at the CloudFront
   distribution. Rebuild the frontend (B11) with the new `VITE_API_BASE_URL` if the API hostname
   changed too.

### B14. Monitoring and logs

Every service logs to CloudWatch Logs (console: **CloudWatch** → **Log groups** →
`/ecs/reo-<service>`), or from the CLI:

```bash
aws logs tail /ecs/reo-api --follow --region eu-west-1
```

**ECS** → your cluster → **Services** shows running/desired task counts and recent deployment
events; **ECS** → cluster → **Tasks** → a task → **Health and metrics** shows CPU/memory.

### B15. Tearing it down

**Do this when you're done evaluating — this deployment costs real money every hour it's up (see
B0).** From the repository root (see B6's note — this is often a fresh terminal session, so `cd`
there explicitly rather than assuming where you left off):

```bash
cd platform/infrastructure/terraform
terraform destroy -var="openrouter_api_key=sk-or-v1-..."
```

Review what it plans to delete, confirm with `yes`. RDS has `deletion_protection = true` by
default — if `destroy` stops on that, disable it first (`terraform apply
-var="deletion_protection=false"` isn't wired as a variable today; simplest is to remove
`deletion_protection = true` from `data.tf`'s `aws_db_instance.postgres` block, `terraform apply`
that one change, then `destroy`). This also deletes the RDS *final snapshot* target only if you
set `skip_final_snapshot = true` first — otherwise AWS keeps one snapshot around (small ongoing
storage cost) as a safety net; delete it manually from the **RDS** → **Snapshots** console page
once you're sure you don't need it.

If you set up the S3/DynamoDB Terraform state backend in B4 and are fully done with this project,
those two resources aren't managed by this Terraform (deliberately, so state doesn't destroy
itself) — delete them manually from the console afterward if wanted.

---

## Part C — Other clouds

The Terraform in this repo is AWS-specific (ECS Fargate, RDS, ElastiCache, ALB, S3+CloudFront,
Secrets Manager). Nothing in the *application* is AWS-specific — every service is a plain Docker
container reading configuration from environment variables (`config.py` in each language), and
`reo_common/secrets.py` already has a `SecretsProvider` interface with local/AWS implementations
that a GCP Secret Manager or Azure Key Vault implementation could sit behind the same way.

The general shape to replicate on another cloud:

| This deployment | GCP equivalent | Azure equivalent |
|---|---|---|
| ECS Fargate (6 services) | Cloud Run (each service, or GKE Autopilot) | Container Apps or AKS |
| RDS PostgreSQL | Cloud SQL for PostgreSQL | Azure Database for PostgreSQL |
| ElastiCache Redis | Memorystore for Redis | Azure Cache for Redis |
| S3 + CloudFront | Cloud Storage + Cloud CDN | Blob Storage + Azure CDN/Front Door |
| Secrets Manager | Secret Manager | Key Vault |
| ALB | Cloud Load Balancing | Application Gateway |

There's no Terraform written for these today — replicating the AWS module structure (network →
data services → app services → frontend, in that order, matching `network.tf`/`data.tf`/`ecs.tf`/
`frontend.tf`) onto the equivalent provider's Terraform resources is the natural path if you need
one of these instead of AWS. The simplest cloud-agnostic alternative for a quick eval deployment on
any provider: any managed container platform that takes a `docker-compose.yml`-shaped input (e.g.
a single beefy VM running `docker compose up -d --build` from `infrastructure/`, behind that
provider's load balancer) gets you running fastest, at the cost of the HA/multi-AZ properties the
AWS Terraform provides.
