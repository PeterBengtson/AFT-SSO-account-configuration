import os
from random import randint
import json
import boto3

step_function_client = boto3.client('stepfunctions')

STATE_MACHINE_ARN = os.environ['STATE_MACHINE_ARN']


def lambda_handler(event, _context):
    print(event)

    message_raw = event['Records'][0]['Sns']['Message']
    message = json.loads(message_raw)

    account_id = message['account_id']
    sso_groups = message.get('sso_groups')
    sso_users = message.get('sso_users')

    message['sso_groups'] = json.loads(sso_groups) if sso_groups else {}
    message['sso_users'] = json.loads(sso_users) if sso_users else {}

    random_number = randint(100000, 999999)
    name = f'configure-{account_id}-sso-permissions-job-{random_number}'

    step_function_client.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=name,
        input=json.dumps(message)
    )

    return True
