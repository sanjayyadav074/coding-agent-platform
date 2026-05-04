output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}"
}

output "ecr_api_repo_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecr_worker_repo_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "app_iam_role_arn" {
  value       = aws_iam_role.app.arn
  description = "Annotate the coding-agent ServiceAccount with this for IRSA"
}

output "secrets_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "conversations_table" {
  value = aws_dynamodb_table.conversations.name
}
