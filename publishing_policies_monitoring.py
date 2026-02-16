import copy
import json
import posixpath

from publishing_policies import check_policy,publish_policy

POLICIES_FILE = "monitoring_uns_policies.json"
ROOT_POLICIES = {
    "Enterprise_C": "b992dcf093661dc3dc966c6a420ac816",
    "Enterprise_B/Site1": "cb60a58c6af2140546396c6435ebb0ea",
    "Enterprise_B/Site2": "16f3fcac07c83e0858e58cf18f0791d6",
    "Enterprise_B/Site3": "17077d9a31f1a81cca8afd9bbda0dd62",
}


def read_file():
    try:
        with open(POLICIES_FILE, 'r') as f:
            return json.load(f)
    except Exception as error:
        raise Exception(f"Failed to read / parse content from {POLICIES_FILE} (Error: {error})")


def prep_policy(base_namespace:str, policy:str, parent:str=None):
    if policy.get("uns").get("namespace"):
        policy["uns"]["namespace"] = posixpath.join(base_namespace, policy["uns"]["namespace"])

    if policy.get("uns").get("description") and '%s' in policy.get("uns").get("description"):
        policy["uns"]["description"] = policy.get("uns").get("description") % base_namespace

    if parent:
        policy["uns"]["parent"] = parent

    if policy.get("uns").get("uns_level") not in ["device", "sensor"]:
        if policy.get("uns").get('dbms'):
            del policy["uns"]["dbms"]
        if policy.get("uns").get("table"):
            del policy["uns"]["table"]
        if policy.get("uns").get("source_node"):
            del policy["uns"]["source_node"]

    elif policy.get("uns").get("uns_level") in ["device", "sensor"]:
        if policy.get("uns").get("source_node"):
            policy["uns"]["where"] = f"node_name='{policy["uns"]["source_node"].strip()}'"
            del policy["uns"]["source_node"]

    if policy.get("uns").get("uns_level") == "sensor":
        if policy.get("uns").get("name"):
            policy["uns"]["column"] = policy["uns"]["name"].lower().strip()
            policy["uns"]["name"] = policy["uns"]["name"].replace('_', ' ')
    policy["uns"]["name"] = f"{base_namespace} - {policy.get('uns').get('name')}"

    return policy




if __name__ =="__main__":
    root_policies = {}
    policies = read_file()
    for root in ROOT_POLICIES:
        print(root)
        policy_id = None
        for sample_policy in policies:
            policy = copy.deepcopy(sample_policy)
            policy = prep_policy(base_namespace=root, policy=policy, parent=policy_id)
            root_policy = root_policies.get(policy.get("uns").get("namespace").rsplit("/", 1)[0])

            policy_id = check_policy(conn="50.116.13.109:32049", policy=policy)
            if policy_id is None:
                publish_policy(conn="50.116.13.109:32049", policy=policy)
                policy_id = check_policy(conn="50.116.13.109:32049", policy=policy)
            if policy.get("uns").get("namespace") and policy.get("uns").get("namespace") not in root_policies:
                root_policies[policy.get("uns").get("namespace")] = policy_id
        exit(1)
