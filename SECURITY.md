# Security Notes

## Threat model
| Asset                    | Threat                                          | Mitigation                                                |
|--------------------------|-------------------------------------------------|-----------------------------------------------------------|
| Slack signing secret     | Forged Slack requests                           | HMAC-SHA256 signature + 5-min timestamp window check      |
| Slack bot token          | Token leak → workspace impersonation            | Stored as `SecretStr`; mounted from k8s Secret / AWS SM   |
| LLM API key              | Quota theft, exfiltration                       | Same as above; no logging of secret values                |
| GitHub token             | Source-code modification                        | Optional, scoped PAT or GitHub App; mock used by default  |
| Pod identity             | Token theft via IMDS                            | NetworkPolicy blocks `169.254.169.254/32`; IRSA only      |
| Container runtime        | Privilege escalation, lateral movement          | Non-root user, RO root FS, no capabilities, seccomp=RuntimeDefault |
| Multi-tenant isolation   | Cross-user data leakage                         | Per-request workflow IDs (`coding-{user}-{uuid}`); no shared mutable state |
| Logging                  | Secret leakage                                  | Pydantic `SecretStr`; structured logs without secret fields |
| Supply chain             | Malicious dependency                            | Pinned minor versions, ECR `scan_on_push = true`, immutable tags |

## Slack request verification
Every request hitting `/slack/events` is verified as described in the Slack
docs (https://api.slack.com/authentication/verifying-requests-from-slack):

```
expected = "v0=" + HMAC_SHA256(signing_secret, "v0:{timestamp}:{body}")
```

Requests fail with `401` if the signature is missing, malformed, mismatched,
or the timestamp is more than 5 minutes off.

## Multi-user isolation
- Each Slack request is assigned a UUID and a workflow ID derived from
  `user_id + uuid`. Temporal therefore guarantees one workflow per request,
  with isolated state and history.
- Activities are stateless and receive only the typed `CodingRequest`. No
  per-user state lives in process memory or shared caches.
- Slack responses go either to the request's `response_url` (slash command)
  or to the originating `channel_id` (events API). One user's response is
  never sent to another.

## Cluster security
- IRSA is used in lieu of long-lived static credentials.
- The chart ships a default-deny NetworkPolicy that allows only DNS, the
  Temporal frontend, and outbound 443.
- Pods run as UID 1000, read-only root filesystem, all caps dropped,
  seccomp `RuntimeDefault`.
- ECR repositories are immutable and scanned on push.


