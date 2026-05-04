data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 3)
  tags = {
    Project     = "coding-agent-platform"
    Environment = var.environment
  }
}

# ─── VPC ──────────────────────────────────────────────────────────────────────
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "${var.name}-vpc"
  cidr = var.vpc_cidr
  azs  = local.azs

  private_subnets = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway      = true
  single_nat_gateway      = true
  enable_dns_hostnames    = true
  map_public_ip_on_launch = false

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
  tags = local.tags
}

# ─── EKS ──────────────────────────────────────────────────────────────────────
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.13"

  cluster_name    = "${var.name}-${var.environment}"
  cluster_version = var.cluster_version

  vpc_id                         = module.vpc.vpc_id
  subnet_ids                     = module.vpc.private_subnets
  cluster_endpoint_public_access = true

  enable_irsa = true

  cluster_addons = {
    coredns            = { most_recent = true }
    kube-proxy         = { most_recent = true }
    vpc-cni            = { most_recent = true }
    aws-ebs-csi-driver = { most_recent = true }
  }

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2_x86_64"
      instance_types = var.node_instance_types
      desired_size   = var.node_desired_size
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      labels         = { workload = "general" }
    }
  }

  tags = local.tags
}

# ─── ECR repositories for the two services ───────────────────────────────────
resource "aws_ecr_repository" "api" {
  name                 = "${var.name}/api"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
  tags = local.tags
}

resource "aws_ecr_repository" "worker" {
  name                 = "${var.name}/worker"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
  tags = local.tags
}

# ─── Secrets Manager: app credentials ────────────────────────────────────────
resource "random_id" "secret_suffix" {
  byte_length = 4
}

resource "aws_secretsmanager_secret" "app" {
  name = "${var.name}/${var.environment}/credentials-${random_id.secret_suffix.hex}"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id     = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(var.secrets_payload)
}

# ─── IAM role for IRSA: lets the app pod read its own secret ─────────────────
data "aws_iam_policy_document" "app_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider}:sub"
      values = [
        "system:serviceaccount:coding-agent:coding-agent",
        "system:serviceaccount:coding-agent:coding-agent-coding-agent",
      ]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = "${var.name}-${var.environment}-app"
  assume_role_policy = data.aws_iam_policy_document.app_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "app_secrets_read" {
  statement {
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
}

resource "aws_iam_policy" "app_secrets_read" {
  name   = "${var.name}-${var.environment}-secrets-read"
  policy = data.aws_iam_policy_document.app_secrets_read.json
}

resource "aws_iam_role_policy_attachment" "app_secrets_read" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.app_secrets_read.arn
}

# ─── DynamoDB: per-user conversation history ─────────────────────────────────
resource "aws_dynamodb_table" "conversations" {
  name         = "${var.name}-${var.environment}-conversations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.tags
}

data "aws_iam_policy_document" "app_dynamodb_rw" {
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.conversations.arn]
  }
}

resource "aws_iam_policy" "app_dynamodb_rw" {
  name   = "${var.name}-${var.environment}-dynamodb-rw"
  policy = data.aws_iam_policy_document.app_dynamodb_rw.json
}

resource "aws_iam_role_policy_attachment" "app_dynamodb_rw" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.app_dynamodb_rw.arn
}
