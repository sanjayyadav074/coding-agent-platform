variable "aws_region" {
  type        = string
  default     = "us-west-2"
  description = "AWS region for all resources"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Deployment environment tag"
}

variable "name" {
  type        = string
  default     = "coding-agent"
  description = "Common name prefix for resources"
}

variable "cluster_version" {
  type    = string
  default = "1.30"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.medium"]
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 5
}

variable "secrets_payload" {
  description = "Slack and provider credentials, stored in AWS Secrets Manager"
  type = object({
    SLACK_BOT_TOKEN      = string
    SLACK_SIGNING_SECRET = string
    GROQ_API_KEY         = string
    GITHUB_TOKEN         = optional(string, "")
  })
  sensitive = true
  default = {
    SLACK_BOT_TOKEN      = ""
    SLACK_SIGNING_SECRET = ""
    GROQ_API_KEY         = ""
    GITHUB_TOKEN         = ""
  }
}
