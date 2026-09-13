variable "atlassian_url" {
  type = string
}

variable "atlassian_email" {
  type = string
}

variable "confluence_root_page_id" {
  type = string
}

variable "atlassian_api_token" {
  type      = string
  sensitive = true
}

variable "schedule_enabled" {
  type    = bool
  default = false
}
