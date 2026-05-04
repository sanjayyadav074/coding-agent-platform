# Example variable file. Copy to terraform.tfvars and fill in.
aws_region          = "us-west-2"
environment         = "dev"
name                = "coding-agent"
node_instance_types = ["t3.medium"]
node_desired_size   = 2
node_min_size       = 2
node_max_size       = 5

secrets_payload = {
  SLACK_BOT_TOKEN      = "xoxb-replace-me"
  SLACK_SIGNING_SECRET = "replace-me"
  GROQ_API_KEY         = "gsk_replace_me"
  GITHUB_TOKEN         = ""
}
