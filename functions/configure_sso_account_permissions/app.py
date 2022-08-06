import os
import boto3

sso = boto3.client('sso-admin')
identity_store = boto3.client('identitystore')

instances = sso.list_instances()['Instances']
if len(instances) != 1:
    raise RuntimeError(f"Number of SSO instances is {len(instances)}, not 1")

SSO_INSTANCE_ARN = instances[0]['InstanceArn']
SSO_IDENTITY_STORE_ID = instances[0]['IdentityStoreId']
CLOUD_ADMINISTRATION_GROUP_NAME =  os.environ['CLOUD_ADMINISTRATION_GROUP_NAME']

KEEPERS = ["AWSSecurityAuditors", "AWSControlTowerAdmins", "AWSSecurityAuditPowerUsers"]
if CLOUD_ADMINISTRATION_GROUP_NAME:
    KEEPERS.append(CLOUD_ADMINISTRATION_GROUP_NAME)


def lambda_handler(data, _context):
    print("Data: ", data)

    account_id = data['account_id']
    sso_groups = normalize(data.get('sso_groups', {}))
    sso_users = normalize(data.get('sso_users', {}))

    # Maps from permission set ARN to name and from name to ARN (for all SSO permissions sets available)
    sso_instance_permission_sets = get_sso_instance_permission_sets()

    # Maps from permission set ARN (for all permission sets deployed to account)
    account_permission_sets = get_account_permission_sets(account_id)

    # The permission sets assigned to the account, divided into sso_groups and sso_users (just like the input)
    account_assignments = get_account_assignments(account_id, account_permission_sets)

    if CLOUD_ADMINISTRATION_GROUP_NAME:
        # Add the group to the account with AWSAdministratorAccess
        assign_group(account_id, CLOUD_ADMINISTRATION_GROUP_NAME, 'AWSAdministratorAccess', sso_instance_permission_sets)

    # Add specified groups with specified permissions
    for sso_group_name, permission_set_names in sso_groups.items():
        for permission_set_name in permission_set_names:
            assign_group(account_id, sso_group_name, permission_set_name, sso_instance_permission_sets)

    # Add specified users with specified permissions
    for sso_user_name, permission_set_names in sso_users.items():
        for permission_set_name in permission_set_names:
            assign_user(account_id, sso_user_name, permission_set_name, sso_instance_permission_sets)

    # Remove groups with permission sets not mentioned in the specifications
    for sso_group_name, permission_set_names in account_assignments['sso_groups'].items():
        if sso_group_name not in KEEPERS:
            for permission_set_name in permission_set_names:
                desired_permission_sets = sso_groups.get(sso_group_name, [])
                if permission_set_name not in desired_permission_sets:
                    unassign_group(account_id, sso_group_name, permission_set_name, sso_instance_permission_sets)

    # Remove users with permission sets not mentioned in the specifications
    for sso_user_name, permission_set_names in account_assignments['sso_users'].items():
        for permission_set_name in permission_set_names:
            desired_permission_sets = sso_users.get(sso_user_name, [])
            if permission_set_name not in desired_permission_sets:
                unassign_user(account_id, sso_user_name, permission_set_name, sso_instance_permission_sets)

    return True


def normalize(dict):
    result = {}
    for principal_name, permission_set_names in dict.items():
        if isinstance(permission_set_names, str):
            permission_set_names = [permission_set_names]
        result[principal_name] = permission_set_names
    return result


def get_sso_instance_permission_sets():
    result = {}
    pm_set_arns = sso.list_permission_sets(
        InstanceArn=SSO_INSTANCE_ARN
    )['PermissionSets']
    for pm_set_arn in pm_set_arns:
        response = sso.describe_permission_set(
            InstanceArn=SSO_INSTANCE_ARN,
            PermissionSetArn=pm_set_arn
        )['PermissionSet']
        name = response['Name']
        # Map both from name to ARN and from ARN to name
        result[pm_set_arn] = name
        result[name] = pm_set_arn
    return result


def get_account_permission_sets(account_id):
    result = {}
    pm_set_arns = sso.list_permission_sets_provisioned_to_account(
        InstanceArn=SSO_INSTANCE_ARN,
        AccountId=account_id
    )['PermissionSets']
    for pm_set_arn in pm_set_arns:
        response = sso.describe_permission_set(
            InstanceArn=SSO_INSTANCE_ARN,
            PermissionSetArn=pm_set_arn
        )['PermissionSet']
        name = response['Name']
        # Map only from ARN to name
        result[pm_set_arn] = name
    return result


def get_account_assignments(account_id, account_permission_sets):
    sso_groups = {}
    sso_users = {}
    for pm_set_arn, pm_set_name in account_permission_sets.items():
        response = sso.list_account_assignments(
            InstanceArn=SSO_INSTANCE_ARN,
            AccountId=account_id,
            PermissionSetArn=pm_set_arn
        )
        for account_assignment in response['AccountAssignments']:
            principal_id = account_assignment['PrincipalId']
            if account_assignment['PrincipalType']== 'GROUP':
                group_name = get_group_name(principal_id)
                if not sso_groups.get(group_name):
                    sso_groups[group_name] = []
                sso_groups[group_name].append(pm_set_name)
            else:
                user_name = get_user_name(principal_id)
                if not sso_users.get(user_name):
                    sso_users[user_name] = []
                sso_users[user_name].append(pm_set_name)
    return {
        "sso_groups": sso_groups,
        "sso_users": sso_users
    }


def assign_group(account_id, sso_group_name, permission_set_name, sso_instance_permission_sets):
    print(f"Assigning SSO Group {sso_group_name} with {permission_set_name} to account {account_id}...")

    permission_set_arn = sso_instance_permission_sets.get(permission_set_name)
    if not permission_set_arn:
        raise RuntimeError(f"Can't find a permission set named {permission_set_name}")
    group_id = get_group_id(sso_group_name)

    sso.create_account_assignment(
        InstanceArn=SSO_INSTANCE_ARN,
        TargetType='AWS_ACCOUNT',
        TargetId=account_id,
        PrincipalType='GROUP',
        PrincipalId=group_id,
        PermissionSetArn=permission_set_arn
    )


def unassign_group(account_id, sso_group_name, permission_set_name, sso_instance_permission_sets):
    print(f"Removing SSO Group {sso_group_name} with permission set {permission_set_name} from account {account_id}...")

    permission_set_arn = sso_instance_permission_sets.get(permission_set_name)
    if not permission_set_arn:
        raise RuntimeError(f"Can't find a permission set named {permission_set_name}")
    group_id = get_group_id(sso_group_name)

    sso.delete_account_assignment(
        InstanceArn=SSO_INSTANCE_ARN,
        TargetType='AWS_ACCOUNT',
        TargetId=account_id,
        PrincipalType='GROUP',
        PrincipalId=group_id,
        PermissionSetArn=permission_set_arn
    )


def assign_user(account_id, sso_user_name, permission_set_name, sso_instance_permission_sets):
    print(f"Assigning SSO User {sso_user_name} with {permission_set_name} to account {account_id}...")

    permission_set_arn = sso_instance_permission_sets.get(permission_set_name)
    if not permission_set_arn:
        raise RuntimeError(f"Can't find a permission set named {permission_set_name}")
    user_id = get_user_id(sso_user_name)

    sso.create_account_assignment(
        InstanceArn=SSO_INSTANCE_ARN,
        TargetType='AWS_ACCOUNT',
        TargetId=account_id,
        PrincipalType='USER',
        PrincipalId=user_id,
        PermissionSetArn=permission_set_arn
    )


def unassign_user(account_id, sso_user_name, permission_set_name, sso_instance_permission_sets):
    print(f"Removing SSO User {sso_user_name} with permission set {permission_set_name} from account {account_id}...")

    permission_set_arn = sso_instance_permission_sets.get(permission_set_name)
    if not permission_set_arn:
        raise RuntimeError(f"Can't find a permission set named {permission_set_name}")
    user_id = get_user_id(sso_user_name)

    sso.delete_account_assignment(
        InstanceArn=SSO_INSTANCE_ARN,
        TargetType='AWS_ACCOUNT',
        TargetId=account_id,
        PrincipalType='USER',
        PrincipalId=user_id,
        PermissionSetArn=permission_set_arn
    )


def get_group_id(name):
    groups = identity_store.list_groups(    
        IdentityStoreId=SSO_IDENTITY_STORE_ID,
        Filters=[
            {
                'AttributePath': 'DisplayName',
                'AttributeValue': name
            },
        ]
    )['Groups']
    for group in groups:
        if group['DisplayName'] == name:
            return group['GroupId']
    raise RuntimeError(f"Can't find an SSO group named {name}")
        

def get_group_name(principal_id):
    return identity_store.describe_group(    
        IdentityStoreId=SSO_IDENTITY_STORE_ID,
        GroupId=principal_id
    )['DisplayName']
    

def get_user_id(name):
    users = identity_store.list_users(    
        IdentityStoreId=SSO_IDENTITY_STORE_ID,
        Filters=[
            {
                'AttributePath': 'UserName',
                'AttributeValue': name
            },
        ]
    )['Users']
    for user in users:
        if user['UserName'] == name:
            return user['UserId']
    raise RuntimeError(f"Can't find an SSO user named {name}")


def get_user_name(principal_id):
    return identity_store.describe_user(    
        IdentityStoreId=SSO_IDENTITY_STORE_ID,
        UserId=principal_id
    )['UserName']
