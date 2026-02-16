import json
from sys import exec_prefix

import requests

POLICIES_FILE = "enterprise_c_uns_policies.json"

def read_file():
    try:
        with open(POLICIES_FILE, 'r') as f:
            return json.load(f)
    except Exception as error:
        raise Exception(f"Failed to read / parse content from {POLICIES_FILE} (Error: {error})")


def prep_policy(policy:dict, parent=None):
    if policy.get("uns").get("source_node"):
        del policy["uns"]["source_node"]
    if policy.get("uns").get("uns_level") == "device":
        if policy["uns"].get("dbms"):
            del policy["uns"]["dbms"]
        if policy["uns"].get("table"):
            del policy["uns"]["table"]

    if parent:
        policy["uns"]["parent"] = parent

    # if policy.get("uns").get("uns_level") == "sensor":
    #     print(policy)
    #     exit(1)
    return policy

def check_policy(conn:str="50.116.13.109:32049", policy:dict=None):
    uns = policy.get("uns").get("namespace")
    name = policy.get("uns").get("name")

    headers = {
        "command": f'blockchain get uns where namespace="{uns}" and name="{name}" bring [*][id]',
        "User-Agent": "AnyLog/1.23"
    }

    try:
        response = requests.get(url=f"http://{conn}", headers=headers)
        response.raise_for_status()
    except Exception as error:
        raise Exception(f"Failed to execute GET against {conn} (Error: {error})")

    policy_id = response.text
    return None if policy_id == "[]" else policy_id

def publish_policy(conn:str="50.116.13.109:32049", policy:dict=None):
    headers = {
        "command": "blockchain insert where policy=!new_policy and local=true and master=!ledger_conn",
        "User-Agent": "AnyLog/1.23"
    }

    try:
        response = requests.post(url=f"http://{conn}", headers=headers, data=f"<new_policy={json.dumps(policy)}>")
        response.raise_for_status()
    except Exception as error:
        raise Exception(f"Failed to execute POST against {conn} (Error: {error})")


if __name__ =="__main__":
    root_policies = {}
    policies = read_file()
    for policy in policies:
        root_policy = root_policies.get(policy.get("uns").get("namespace").rsplit("/", 1)[0])

        policy_id = check_policy(conn="50.116.13.109:32049", policy=policy)
        # print(policy_id)
        if policy_id is None:
            policy = prep_policy(policy=policy, parent=root_policy)
        # print(policy)
        # exit(1)
            publish_policy(conn="50.116.13.109:32049", policy=policy)
        policy_id = check_policy(conn="50.116.13.109:32049", policy=policy)
        if policy.get("uns").get("namespace") and policy.get("uns").get("namespace") not in root_policies:
            root_policies[policy.get("uns").get("namespace")] = policy_id

