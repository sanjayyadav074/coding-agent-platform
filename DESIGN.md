# Design Notes

This document explains the architecture decisions, implementation details, intentional trade-offs, and future improvements for the Coding Agent Platform.

## Project Goals

1. **Slack-native UX**: Users invoke the agent with a slash command or @mention; replies show up in the same channel or thread.

2. **Durable orchestration**: Long-running LLM and GitHub operations survive pod restarts and are observable end-to-end.

3. **Multi-user safety**: Concurrent users never see each other's data; per-user conversation memory with privacy isolation.

4. **Cloud-native deployment**: First-class container images, Helm chart, and Terraform that provisions an EKS cluster from scratch.

5. **Reproducibility**: `make compose-up` brings the full stack up locally; `terraform apply` plus `helm upgrade` deploys to AWS with zero manual configuration.

## High-level architecture
```
┌────────┐  signed POST  ┌────────────────┐  start_workflow  ┌──────────────────┐
│ Slack  │──────────────▶│ FastAPI (api)  │─────────────────▶│ Temporal Server  │
└────────┘               └────────────────┘                  └──────────────────┘
     ▲                           │                                    │
     │     chat.postMessage      │                                    ▼
     │     / response_url        │                            ┌────────────────┐
     └───────────────────────────┴───────────────────────────▶│ Temporal Worker │
                                                              │  (activities)   │
                                                              └────────┬────────┘
                                                                       │
                                              ┌────────────────────────┼─────────────────────┐
                                              ▼                        ▼                     ▼
                                       Pydantic AI Agent        GitHub (real or mock)   Slack Webhook
                                              │
                                              ▼
                                         LLM provider
                                          (Groq/…)
```
A rendered Mermaid diagram is in [`docs/architecture.md`](docs/architecture.md).

## Why these technologies?
- **Pydantic AI** — type-safe tool definitions, retries, model-agnostic.
  The agent's GitHub tools are typed methods, which the LLM is forced to
  call with valid arguments.
- **Temporal** — durable execution gives us automatic retries, replayable
  history, deterministic state, and free observability through the Web UI.
  The LLM call lives in an activity (non-deterministic IO), the
  orchestration lives in the workflow.
- **FastAPI** — fast, ASGI-native, easy Slack signature middleware.
- **EKS + Helm + Terraform** — industry-standard combo. ECR + IRSA give us
  short-lived credentials. The Helm chart is generic enough to swap in any
  registry / ingress class.
- **Groq (default)** — free tier, low latency. Swap by setting
  `LLM_MODEL=anthropic:claude-3-5-sonnet`, etc.; Pydantic AI handles the rest.

## Multi-user isolation
- One Temporal workflow per Slack request: `coding-{user_id}-{uuid4}`.
- Activities are stateless; the only shared resource is the model client
  (which has no per-tenant state).
- Response delivery uses Slack's `response_url` when present, otherwise
  `chat.postMessage` scoped to the original `channel_id`.

## Conversation memory
- **Per-user conversation history** is stored in DynamoDB, keyed solely by
  `user_id`. This gives each user private, isolated memory that survives
  pod restarts and workflow retries.
- The `ConversationStore` class uses `aioboto3` for async DynamoDB access
  from Temporal activities. History is fetched before each LLM call and
  injected as a plain-text prefix (provider-agnostic, no
  vendor-specific message objects stored).
- **FIFO trim** at 20 turns (configurable via `HISTORY_MAX_TURNS`):
  oldest turns are dropped when the limit is reached.
- **30-day TTL** on all items: DynamoDB automatically purges stale
  conversations.
- **Reset command**: users can send `reset`, `clear`, `forget`, or
  `/reset` to wipe their history and start fresh.
- **Graceful degradation**: if DynamoDB is unreachable, the workflow
  logs a warning and continues without history rather than failing.

## Reliability & retries
- Workflow retry policy: exponential backoff, 4 attempts, with
  `ValueError`/`PermissionError` marked non-retryable.
- Activity heartbeats during the LLM call give Temporal early visibility
  into stuck workers.
- API acknowledges Slack within ~10 ms (well inside the 3 s SLA) before
  starting the workflow.

## Security highlights
See [SECURITY.md](SECURITY.md) for the full list. In short: signed Slack
requests, `SecretStr` everywhere, IRSA-only credentials, NetworkPolicy
that blocks IMDS, hardened pod SecurityContext, immutable ECR tags.

## Observability
- JSON logs via `structlog` on stdout (Cloud-native log collectors do the
  rest).
- Prometheus metrics at `GET /metrics` (request counter + latency).
- Pod annotations let any standard Prometheus operator scrape pods.
- Temporal Web UI for workflow histories.

## Current Limitations and Known Issues

### LLM Call Idempotency
The LLM call itself is not idempotent; retries may cost extra tokens. We bound this with `max_attempts=4` and short timeouts. A production system would implement request deduplication at the Temporal workflow level using workflow IDs.

### Temporal Deployment
The chart deploys against an existing Temporal install. For this implementation, we use a single-binary `temporalio/temporal:latest start-dev` deployment for simplicity. Production deployments should use:
- Temporal Cloud (managed service), or
- Self-hosted Temporal with Cassandra/PostgreSQL backend and proper replication

### LLM Provider Rate Limits
Free Groq tier has rate limits (30 requests per minute). Production deployments should use paid tier or implement request queuing and backoff.

### Agent Context Window
The current implementation uses a simple FIFO trim at 20 turns. Large conversations may exceed model context windows. Future versions should implement semantic chunking or summarization.

---

## Intentional Trade-offs 

The following architectural decisions were made to balance production-readiness with project scope. Each section describes what was implemented, why the trade-off was acceptable, and what a full production deployment should include.

### Trade-off 1: Single NAT Gateway (Not Highly Available)

**What We Implemented:**
```
VPC Architecture (Current):
┌─────────────────────────────────────────────────────────┐
│ AWS VPC: 10.20.0.0/16                                   │
│                                                         │
│  Availability Zones:                                    │
│  ┌─── us-east-1a ───┬─── us-east-1b ───┬─── us-1c ───┐ │
│  │                  │                  │              │ │
│  │ PUBLIC SUBNET    │ PUBLIC SUBNET    │ PUBLIC       │ │
│  │ 10.20.128.0/20   │ 10.20.144.0/20   │ 10.20.160/20 │ │
│  │ - NAT Gateway    │ - (none)         │ - (none)     │ │
│  │ - ALB listener   │ - ALB listener   │ - ALB        │ │
│  │ - IGW route      │ - IGW route      │ - IGW route  │ │
│  │                  │                  │              │ │
│  ├──────────────────┼──────────────────┼──────────────┤ │
│  │                  │                  │              │ │
│  │ PRIVATE SUBNET   │ PRIVATE SUBNET   │ PRIVATE      │ │
│  │ 10.20.0.0/20     │ 10.20.16.0/20    │ 10.20.32/20  │ │
│  │ - EKS nodes      │ - EKS nodes      │ - EKS nodes  │ │
│  │ - API pods       │ - API pods       │ - API pods   │ │
│  │ - Worker pods    │ - Worker pods    │ - Worker     │ │
│  │                  │                  │              │ │
│  │ Route: 0.0.0.0/0 → NAT in us-east-1a (single)      │ │
│  │        ↑─────────────┴──────────────┴──────────────┘ │
│  └──────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘

Routing tables:
  Private subnet 1a: 0.0.0.0/0 → nat-xxxxx (in 1a)
  Private subnet 1b: 0.0.0.0/0 → nat-xxxxx (in 1a) [cross-AZ]
  Private subnet 1c: 0.0.0.0/0 → nat-xxxxx (in 1a) [cross-AZ]
```

**Why This Is Acceptable:**
- Bandwidth: Single NAT Gateway handles 45 Gbps, far exceeding this workload
- Simplicity: Terraform configuration is simpler for demonstration purposes

**Production Risk:**
If us-east-1a fails, ALL private subnets lose internet access. Pods in us-east-1b and us-east-1c cannot reach Slack, GitHub, or Groq APIs. Workflows would fail until the AZ recovers.

**Production Solution:**
```
High-Availability NAT Architecture (Recommended):
┌─────────────────────────────────────────────────────────┐
│  ┌─── us-east-1a ───┬─── us-east-1b ───┬─── us-1c ───┐ │
│  │                  │                  │              │ │
│  │ PUBLIC SUBNET    │ PUBLIC SUBNET    │ PUBLIC       │ │
│  │ - NAT GW #1      │ - NAT GW #2      │ - NAT GW #3  │ │
│  │                  │                  │              │ │
│  ├──────────────────┼──────────────────┼──────────────┤ │
│  │                  │                  │              │ │
│  │ PRIVATE SUBNET   │ PRIVATE SUBNET   │ PRIVATE      │ │
│  │ Route:           │ Route:           │ Route:       │ │
│  │ 0.0.0.0/0 →      │ 0.0.0.0/0 →      │ 0.0.0.0/0 →  │ │
│  │ NAT GW #1 (AZ-local)  NAT GW #2 (AZ-local)  NAT #3 │ │
│  └──────────────────┴──────────────────┴──────────────┘ │
└─────────────────────────────────────────────────────────┘
```

Terraform configuration for HA NAT:
```hcl
module "vpc" {
  enable_nat_gateway     = true
  single_nat_gateway     = false  # Enable multiple NAT Gateways
  one_nat_gateway_per_az = true   # One per AZ for high availability
}
```

Benefits:
- Survives AZ failure: pods in us-east-1b continue using NAT GW #2
- Lower latency: no cross-AZ data transfer for outbound traffic
- Better bandwidth distribution across availability zones

---

### Trade-off 2: No Distributed Tracing Enabled

**What We Implemented:**
```
Current Observability Stack:
┌─────────────────────────────────────────────┐
│ IMPLEMENTED:                                │
│ - Structured JSON logs via structlog        │
│ - Prometheus metrics at /metrics endpoint   │
│ - Health checks: /healthz and /readyz       │
│ - Temporal Web UI for workflow inspection   │
│                                             │
│ NOT CONFIGURED:                             │
│ - OpenTelemetry trace collection            │
│ - Distributed tracing backend (Jaeger/Tempo)│
│ - Span correlation across services          │
│ - Visual request flow timeline              │
└─────────────────────────────────────────────┘
```

Code is instrumentation-ready but not configured:
```python
# .env.example
OTEL_EXPORTER_OTLP_ENDPOINT=  # Empty - not configured
```

**Why This Is Acceptable:**
- Logs and metrics provide sufficient visibility for demo purposes
- Adding a full tracing stack (Jaeger or Tempo) adds operational complexity
- The code is already instrumented to accept OTEL_EXPORTER_OTLP_ENDPOINT

**Current Debugging Experience:**
```
Logs (grep-based correlation via request_id):
[14:23:15] {"event": "workflow.start", "request_id": "abc123"}
[14:23:16] {"event": "agent.run", "request_id": "abc123", "duration_ms": 1234}
[14:23:17] {"event": "slack.post", "request_id": "abc123"}

Manual correlation required; no visual timeline
```

**Production Solution:**

Full OpenTelemetry instrumentation:
```python
# packages/coding_agent/src/coding_agent/tracing.py
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

def init_tracing(endpoint: str):
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

# services/worker/activities.py
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@activity.defn(name="run_agent")
async def run_agent(payload: dict[str, Any]) -> dict[str, Any]:
    with tracer.start_as_current_span("run_agent") as span:
        span.set_attribute("user_id", req.user_id)
        span.set_attribute("request_id", req.request_id)
        
        with tracer.start_as_current_span("dynamodb.load_history"):
            prior_turns = await store.load(req.user_id)
        
        with tracer.start_as_current_span("llm.chat_completion"):
            result = await agent.run(...)
        
        return result
```

Deploy tracing backend:
```bash
# Option A: Jaeger (self-hosted)
helm install jaeger jaegertracing/jaeger --namespace observability

# Option B: Grafana Tempo (self-hosted)
helm install tempo grafana/tempo --namespace observability

# Option C: Honeycomb (SaaS)
export OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io
export HONEYCOMB_API_KEY=<key>
```

Enable in deployment:
```yaml
# deploy/helm/coding-agent/values.yaml
config:
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://jaeger-collector.observability:4317"
```

Resulting trace visualization:
```
Request: /coding write fizzbuzz (2.3s total)
├─ [api] POST /slack/events (12ms)
│  └─ verify_slack_signature (3ms)
├─ [temporal] start_workflow (45ms)
├─ [worker] CodingWorkflow (2.1s)
│  ├─ post_slack_ack (120ms)
│  │  └─ POST hooks.slack.com (95ms) [external]
│  ├─ run_agent (1.8s)
│  │  ├─ dynamodb.load_history (45ms) [identifies latency spike]
│  │  ├─ llm.chat_completion (1.6s)
│  │  │  └─ POST api.groq.com (1.55s) [external]
│  │  └─ memory.render_prefix (5ms)
│  ├─ save_history (35ms)
│  │  └─ dynamodb.put_item (28ms)
│  └─ post_slack_result (80ms)

Visual timeline shows 95% of latency is external API calls
```

Benefits:
- Visual request flow across all services
- Automatic context propagation (trace_id follows entire request)
- Identify bottlenecks instantly (e.g., 95% time in Groq API)
- Correlate errors across service boundaries

---

### Trade-off 3: Local Terraform State (No Remote Backend)

**What We Implemented:**
```
Current state storage:
infra/terraform/
├── terraform.tfstate          # Local file, single source of truth
├── terraform.tfstate.backup   # Previous version backup
├── .terraform/                # Provider plugins
└── .terraform.lock.hcl        # Provider version lock
```

State is stored in local files, .gitignored to prevent accidental commits.

**Why This Is Acceptable:**
- Single developer workflow (no team collaboration needed)
- Simplifies initial setup (no S3 bucket prerequisite)
- State file is .gitignored, so secrets won't leak via git
- Demonstrates infrastructure-as-code without operational overhead

**Problems with Local State for Production:**

1. No team collaboration:
   ```
   Developer A: terraform apply  # Creates EKS cluster
   Developer B: terraform apply  # Different state, tries to create cluster again
   Result: ERROR "resource already exists"
   ```

2. No state locking:
   ```
   Developer A: terraform apply (in progress)
   Developer B: terraform apply (starts simultaneously)
   Result: State corruption - both writing to same file
   ```

3. No backup/versioning:
   ```
   $ rm terraform.tfstate  # Accidental deletion
   Result: Lost all state, cannot manage resources anymore
   ```

4. Secrets in plaintext state:
   ```json
   {
     "resources": [{
       "type": "aws_secretsmanager_secret_version",
       "instances": [{
         "attributes": {
           "secret_string": "{\"SLACK_BOT_TOKEN\":\"xoxb-actual-token\"}"
         }
       }]
     }]
   }
   ```
   Even though .gitignored, local state contains secrets in plaintext.

**Production Solution:**

S3 backend with DynamoDB locking:

Step 1: Create backend resources (one-time setup):
```hcl
# infra/terraform-backend/main.tf (separate directory)
resource "aws_s3_bucket" "terraform_state" {
  bucket = "my-company-terraform-state"
  
  versioning {
    enabled = true  # Keep history of all state changes
  }
  
  server_side_encryption_configuration {
    rule {
      apply_server_side_encryption_by_default {
        sse_algorithm = "AES256"
      }
    }
  }
  
  lifecycle {
    prevent_destroy = true  # Safety: prevent accidental deletion
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "terraform_lock" {
  name         = "terraform-state-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  
  attribute {
    name = "LockID"
    type = "S"
  }
}
```

Step 2: Configure backend in main Terraform:
```hcl
# infra/terraform/backend.tf
terraform {
  backend "s3" {
    bucket         = "my-company-terraform-state"
    key            = "coding-agent/prod/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-state-lock"
  }
}
```

Step 3: Migrate existing state:
```bash
cd infra/terraform
terraform init -migrate-state
# Terraform will copy local state to S3
```

Locking flow:
```
Developer A: terraform apply
              ↓
         1. Acquire lock in DynamoDB
            (writes LockID = "coding-agent/prod/terraform.tfstate")
              ↓
         2. Download state from S3
              ↓
         3. Execute infrastructure changes
              ↓
         4. Upload new state to S3 (creates new version)
              ↓
         5. Release lock in DynamoDB

Developer B: terraform apply (during A's execution)
              ↓
         Try to acquire lock
              ↓
         ERROR: "Lock already held by Developer A (ID: abc123)"
              ↓
         Wait or abort
```

Version history in S3:
```
s3://my-company-terraform-state/coding-agent/prod/terraform.tfstate

Version history:
- v15 (current)   2026-05-03 16:00   Added autoscaling policies
- v14             2026-05-03 15:45   DynamoDB conversation table
- v13             2026-05-03 15:30   Worker HPA increased to max=10
- v12             2026-05-03 15:15   ECR repositories created
...

Rollback capability:
aws s3api get-object --bucket <bucket> --key <key> --version-id v14 terraform.tfstate
```

Benefits:
- Team collaboration: everyone reads/writes the same remote state
- State locking: DynamoDB prevents concurrent modifications
- Versioning: rollback to any previous state version
- Encryption: state encrypted at rest in S3
- Audit: CloudTrail logs every state file access

Migration timeline:
- Day 1 of production work: migrate to S3 backend
- Before adding second team member: ensure locking is active

---

## Additional Future Improvements

### High Availability Enhancements

**Multi-Region Failover:**
Current deployment is single-region (us-east-1). For global availability:
- Deploy to multiple regions (us-east-1, eu-west-1, ap-southeast-1)
- Route 53 health checks with latency-based routing
- DynamoDB Global Tables for cross-region replication
- Multi-region ECR replication

**EKS Control Plane:**
Already multi-AZ by AWS design. For additional resilience:
- Use Cluster Autoscaler for node scaling across AZs
- Implement Pod Topology Spread Constraints to force distribution
- Add Karpenter for advanced node lifecycle management

### Security Enhancements

**Pod Security Standards:**
Current: Basic SecurityContext with non-root user. Production should enforce:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: coding-agent
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

**Network Policies - Service Mesh:**
Current NetworkPolicy is L4 (IP/port). For L7 security:
- Deploy Istio or Linkerd service mesh
- mTLS between all services
- Fine-grained authorization policies per service

**Secrets Rotation:**
Current: Manual secret updates via terraform apply. Production should use:
```hcl
resource "aws_secretsmanager_secret" "app" {
  rotation_rules {
    automatically_after_days = 30
  }
}

resource "aws_lambda_function" "secret_rotation" {
  # Lambda that rotates Slack/GitHub/API keys
}
```

**Image Vulnerability Scanning:**
Current: ECR scan-on-push enabled. Additional hardening:
- Integrate Trivy or Snyk in CI pipeline
- Fail builds on HIGH/CRITICAL vulnerabilities
- Regular base image updates (Dependabot for Dockerfiles)

### Scalability Improvements

**Database Performance:**
Current DynamoDB configuration is PAY_PER_REQUEST (auto-scales). For predictable workloads:
- Switch to provisioned capacity with auto-scaling
- Add DynamoDB Accelerator (DAX) for read caching
- Implement Time-To-Live (TTL) already enabled, but add CloudWatch alarms for item count

**Caching Layer:**
Add Redis for:
- Agent response caching (identical requests within time window)
- Rate limiting per user
- Session state if expanding beyond stateless workflows

**CDN for Static Assets:**
If adding web UI:
- CloudFront distribution for JS/CSS/images
- S3 bucket for static asset storage
- Gzip/Brotli compression

### Operational Excellence

**GitOps Deployment:**
Current: Manual helm upgrade. Production should use:
- Argo CD for declarative GitOps
- All Kubernetes manifests in git
- Automated sync on git push
- Rollback via git revert

**Preview Environments:**
For each pull request:
- Ephemeral EKS cluster or namespace
- Deploy full stack for testing
- Automatic cleanup after PR merge/close

**Chaos Engineering:**
Test failure modes:
- Chaos Mesh to inject pod failures, network latency
- GameDays to practice incident response
- Regular failover drills (kill NAT Gateway, AZ failure simulation)

### Developer Experience

**Local Development with Tilt:**
Current: docker-compose works but diverges from production. Tilt provides:
- Hot-reload: file changes instantly reflected in local Kubernetes
- Resource dashboard: see all services in browser
- Production parity: runs same Helm charts locally (kind/minikube)

**Debugging Tools:**
- Telepresence: debug local code against remote EKS cluster
- kubectl debug: ephemeral debug containers in pods
- stern: tail logs from multiple pods with filtering

### Cost Optimization

**Spot Instances for Worker Nodes:**
Current: On-demand t3.medium instances. Workers are fault-tolerant (Temporal automatically retries), so:
```hcl
eks_managed_node_groups = {
  spot = {
    instance_types = ["t3.medium", "t3a.medium"]
    capacity_type  = "SPOT"  # Up to 90% cost savings
    desired_size   = 3
  }
}
```

**Right-Sizing:**
Current resource requests are conservative. Production should:
- Enable Vertical Pod Autoscaler (VPA) to recommend optimal requests/limits
- Use Goldilocks to visualize resource recommendations
- Periodic review of actual usage vs allocated resources

**Idle Resource Cleanup:**
- Automatic shutdown of preview environments after 2 hours
- Scale-to-zero for non-production environments overnight
- Reserved Instances or Savings Plans for baseline capacity

---

## Technology Deep Dive

### Pydantic AI Implementation

The agent uses Pydantic AI's type-safe tool system:
```python
agent: Agent[AgentDeps, str] = Agent(
    model="groq:llama-3.3-70b-versatile",
    deps_type=AgentDeps,             # Dependency injection container
    system_prompt=SYSTEM_PROMPT,     # Behavioral constraints
    retries=2,                       # Auto-retry transient failures
)

@agent.tool
async def list_files(ctx: RunContext[AgentDeps], path: str = "") -> str:
    """List files in the user's default GitHub repository at path."""
    action = await ctx.deps.github.list_files(ctx.deps.default_repo, path)
    return action.detail
```

Key design decisions:
- Tools are fully typed (path: str enforced at runtime)
- Dependency injection allows mocking in tests (inject MockGitHubClient)
- Async tools for non-blocking I/O
- Docstrings become part of function-calling schema sent to LLM

### Temporal Workflow Patterns

Workflows are deterministic orchestration; activities are side-effecting operations:
```python
@workflow.defn
class CodingWorkflow:
    @workflow.run
    async def run(self, request: CodingRequest) -> None:
        # Deterministic workflow logic (replayed on worker restart)
        await workflow.execute_activity(post_slack_ack, ...)
        result = await workflow.execute_activity(run_agent, ...)
        await workflow.execute_activity(save_history, ...)
        await workflow.execute_activity(post_slack_result, ...)

@activity.defn
async def run_agent(payload: dict) -> dict:
    # Non-deterministic: LLM call, DynamoDB access
    # Temporal retries automatically on failure
    ...
```

Workflow durability: if a worker pod crashes mid-execution, Temporal replays the workflow from history. Activities already completed are not re-executed (idempotency via activity ID).

### Infrastructure Layers

**Layer 1: Network (VPC)**
- Private subnets for workloads (no public IPs)
- Public subnets for load balancers and NAT Gateway
- VPC endpoints for AWS services (S3, DynamoDB) to avoid internet routing

**Layer 2: Compute (EKS)**
- Managed node groups for simplified operations
- IRSA (IAM Roles for Service Accounts) for pod-level AWS permissions
- Cluster Autoscaler scales nodes based on pod requests

**Layer 3: Application (Helm)**
- Single chart deploys api + worker + service + ingress
- ConfigMap for environment variables
- Secret or ExternalSecret for credentials
- HorizontalPodAutoscaler scales pods based on CPU

**Layer 4: Data (DynamoDB)**
- PAY_PER_REQUEST billing mode for unpredictable load
- Point-in-time recovery enabled (35-day retention)
- Server-side encryption with AWS-managed keys

---

## Conclusion

This platform demonstrates production-ready architecture within take-home assignment constraints. The intentional trade-offs (single NAT Gateway, no distributed tracing, local Terraform state) are explicitly documented and have clear upgrade paths.

For deployment beyond demonstration purposes, prioritize:
1. S3 backend for Terraform (Day 1 of team collaboration)
2. High-availability NAT Gateway (before launch)
3. OpenTelemetry tracing (when debugging performance issues)
