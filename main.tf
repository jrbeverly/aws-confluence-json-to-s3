terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-west-2"
}

locals {
  name = "confluence-json-to-s3"
}

resource "aws_s3_bucket" "published" {
  bucket_prefix = "${local.name}-"
  force_destroy = true
}

resource "aws_secretsmanager_secret" "atlassian" {
  name_prefix             = "${local.name}-"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "atlassian" {
  secret_id     = aws_secretsmanager_secret.atlassian.id
  secret_string = var.atlassian_api_token
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_permissions" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.published.arn}/*"]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.atlassian.arn]
  }

  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda_exec" {
  role   = aws_iam_role.lambda_exec.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

resource "aws_lambda_function" "publisher" {
  function_name = local.name
  filename      = ".build/lambda.zip"
  handler       = "handler.handler"
  runtime       = "python3.11"
  architectures = ["x86_64"]
  role          = aws_iam_role.lambda_exec.arn
  timeout       = 60

  environment {
    variables = {
      ATLASSIAN_URL           = var.atlassian_url
      ATLASSIAN_EMAIL         = var.atlassian_email
      CONFLUENCE_ROOT_PAGE_ID = var.confluence_root_page_id
      S3_BUCKET               = aws_s3_bucket.published.id
      ATLASSIAN_SECRET_ARN    = aws_secretsmanager_secret.atlassian.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda, aws_secretsmanager_secret_version.atlassian]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.publisher.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "publisher" {
  name  = local.name
  state = var.schedule_enabled ? "ENABLED" : "DISABLED"

  schedule_expression = "rate(5 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.publisher.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

output "function_name" {
  value = aws_lambda_function.publisher.function_name
}

output "bucket" {
  value = aws_s3_bucket.published.id
}
