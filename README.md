# SSO Account Configuration

Allows you to use AFT (Account Factory for Terraform) to declaratively specify SSO Group and
SSO User access to an account in the following way:

```terraform
module "john-doe-account" {
  source = "./modules/aft-account-request"
  control_tower_parameters = {
    AccountEmail              = "accounts@example.com"
    AccountName               = "JohnDoeAccount"                                
    ManagedOrganizationalUnit = "Sandbox"  
    SSOUserEmail              = "accounts@example.com"
    SSOUserFirstName          = "Admin"
    SSOUserLastName           = "User"
  }

  custom_fields = {
    "sso_groups" = jsonencode({
      "an-sso-group-you-have-defined" = ["DeveloperAccess", "AWSReadOnlyAccess"]
      "another-sso-group"             = ["SomeOtherPermissionSet", "AnotherPermissionSet"]
      "yet-another-sso-group"         = "YetAnotherPermissionSet"
    })
    "sso_users" = jsonencode({
      "john.doe@example.com"          = ["FooAccess", "BarAccess", "BazAccess"]
      "lisa.doe@example.com"          = "AWSReadOnlyAccess"
    })
  }
}
```

Furthermore, if you provide a value for the parameter `CloudAdministrationGroupName`, this
SSO group will be automatically added to all accounts, with the permissions given in the
parameter `CloudAdministrationGroupPermissionSets`, defaulting to 
`"AWSAdministratorAccess,AWSReadOnlyAccess"`.

NB: Any group or user assignments not explicitly mentioned will be deleted automatically, except for
the groups `AWSSecurityAuditors`, `AWSControlTowerAdmins` and `AWSSecurityAuditPowerUsers`. 
They are assigned by Service Catalog when Control Tower creates an account and should be 
left as is.


## Installation

Deploy this SAM project in the organisation account, in your main region. All that's required
is
```
sam build
sam deploy --guided
```
Subsequent deploys are done just by `sam build && sam deploy`.

To activate, put the following in your `aft-global-customizations` repo, in `pre-api-helpers.sh`
in the `api_helpers` directory. Substitute the `--topic-arn` value for the SNS topic.

```bash
#!/bin/bash -e
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#

echo "Executing Pre-API Helpers"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "Obtaining SSO Groups for account $ACCOUNT_ID..."
SSO_GROUPS=$(aws ssm get-parameters --names /aft/account-request/custom-fields/sso_groups --query "Parameters[0].Value")
echo "SSO Groups: $SSO_GROUPS"

echo "Obtaining SSO Users for account $ACCOUNT_ID..."
SSO_USERS=$(aws ssm get-parameters --names /aft/account-request/custom-fields/sso_users --query "Parameters[0].Value")
echo "SSO Users: $SSO_USERS"

echo "Posting SNS message to configure the account $ACCOUNT_ID for SSO access..."
aws sns publish --topic-arn "arn:aws:sns:xx-xxxx-1:111122223333:aft-sso-account-configuration-topic" \
  --message "{\"account_id\": \"$ACCOUNT_ID\", \"sso_groups\": $SSO_GROUPS, \"sso_users\": $SSO_USERS}"
```


## Protecting the settings

You will probably want to include something like the following in an SCP to protect the AFT settings 
from being tampered with:
```json
{
  "Sid": "DenyAFTCustomFieldsModification",
  "Effect": "Deny",
  "Action": [
    "ssm:DeleteParameter*",
    "ssm:PutParameter"
  ],
  "Resource": "arn:aws:ssm:*:*:parameter/aft/account-request/custom-fields/*",
  "Condition": {
    "ArnNotLike": {
      "aws:PrincipalArn": [
        "arn:aws:iam::*:role/AWSControlTowerExecution",
        "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_AWSAdministratorAccess_*",
        "arn:aws:iam::*:role/stacksets-exec-*",
        "arn:aws:iam::*:role/AWSAFTService",
        "arn:aws:iam::*:role/AWSAFTExecution"
      ]
    }
  }
}
```

You can add the following to the same SCP to block users of a permission set from using or 
even seeing the values of the SSO parameters in their own accounts. Substitute `DeveloperAccess`
with the name of your own permission set, but keep the prefix and wildcard characters:
```json
{
  "Sid": "DenyAFTCustomFieldsUseAndVisibility",
  "Effect": "Deny",
  "Action": [
    "ssm:DeleteParameter*",
    "ssm:DescribeParameters",
    "ssm:GetParameter*",
    "ssm:PutParameter"
  ],
  "Resource": "arn:aws:ssm:*:*:parameter/aft/account-request/custom-fields/*",
  "Condition": {
    "ArnLike": {
      "aws:PrincipalArn": [
        "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_DeveloperAccess_*"
      ]
    }
  }
}
```
