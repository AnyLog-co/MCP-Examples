#!/bin/bash
# =============================================================================
# load_wits_uns_policies.al
# AnyLog script to load WITS-compliant UNS policies into the blockchain
#
# WITS Hierarchy:
#   wits  (root)
#   └── record01  (namespace - WITS Record 1: General Time-Based Drilling Data)
#       ├── RIG-TX-001  (rig - Permian Basin, West Texas, land)
#       │   ├── timestamp, rig_id, activity, status
#       │   ├── bit_depth (item 7), measured_depth (item 8), true_vertical_depth (item 9)
#       │   ├── rop (item 10), block_height (item 11), hookload (item 12)
#       │   ├── wob (item 14), hole_depth (item 15), rpm (item 16), torque (item 17)
#       │   ├── standpipe_pressure (item 18), choke_pressure (item 20), flow_rate (item 24)
#       │   ├── mud_weight_in (item 27), mud_weight_out (item 28)
#       │   ├── mud_temp_in (item 29), mud_temp_out (item 30), total_gas (item 38)
#       ├── RIG-TX-007  (rig - Eagle Ford, South Texas, land)  [same tags]
#       ├── RIG-ND-012  (rig - Bakken, North Dakota, land)     [same tags]
#       └── RIG-GOM-023 (rig - Deepwater, Gulf of Mexico, offshore) [same tags]
#
# Usage:
#   Run from AnyLog CLI:  process !anylog_path/scripts/load_wits_uns_policies.al
#   Or via REST:          curl -X POST http://<node>:<port>/run/script -d "file=!anylog_path/scripts/load_wits_uns_policies.al"
# =============================================================================

# ---------------------------------------------------------------------------
# LAYER 1: WITS Root Policy
# ---------------------------------------------------------------------------
<new_policy = {
    "wits" : {
        "name"        : "wits",
        "uns_layer"   : "root",
        "namespace"   : "wits",
        "standard"    : "WITS Level 0",
        "version"     : "1.0",
        "description" : "Wellsite Information Transfer Specification - Root Namespace",
        "ledger"      : "global"
    }
}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ---------------------------------------------------------------------------
# LAYER 2: WITS Record 01 Namespace
# ---------------------------------------------------------------------------
<new_policy = {
    "wits" : {
        "name"        : "record01",
        "uns_layer"   : "namespace",
        "namespace"   : "wits/record01",
        "parent"      : "wits",
        "wits_record" : "1",
        "description" : "WITS Record 1 - General Time-Based Drilling Data",
        "dbms"        : "timbergrove",
        "table"       : "rig_data",
        "ledger"      : "global"
    }
}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ---------------------------------------------------------------------------
# LAYER 3: Rig Nodes
# ---------------------------------------------------------------------------

# --- RIG-TX-001 : Permian Basin ---
<new_policy = {
    "wits" : {
        "name"      : "RIG-TX-001",
        "uns_layer" : "rig",
        "namespace" : "wits/record01/RIG-TX-001",
        "parent"    : "wits/record01",
        "rig_id"    : "RIG-TX-001",
        "rig_name"  : "Permian Basin",
        "region"    : "West Texas",
        "location"  : "land",
        "dbms"      : "timbergrove",
        "table"     : "rig_data",
        "where"     : "rig_id='RIG-TX-001'",
        "ledger"    : "global"
    }
}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# --- RIG-TX-007 : Eagle Ford ---
<new_policy = {
    "wits" : {
        "name"      : "RIG-TX-007",
        "uns_layer" : "rig",
        "namespace" : "wits/record01/RIG-TX-007",
        "parent"    : "wits/record01",
        "rig_id"    : "RIG-TX-007",
        "rig_name"  : "Eagle Ford",
        "region"    : "South Texas",
        "location"  : "land",
        "dbms"      : "timbergrove",
        "table"     : "rig_data",
        "where"     : "rig_id='RIG-TX-007'",
        "ledger"    : "global"
    }
}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# --- RIG-ND-012 : Bakken ---
<new_policy = {
    "wits" : {
        "name"      : "RIG-ND-012",
        "uns_layer" : "rig",
        "namespace" : "wits/record01/RIG-ND-012",
        "parent"    : "wits/record01",
        "rig_id"    : "RIG-ND-012",
        "rig_name"  : "Bakken",
        "region"    : "North Dakota",
        "location"  : "land",
        "dbms"      : "timbergrove",
        "table"     : "rig_data",
        "where"     : "rig_id='RIG-ND-012'",
        "ledger"    : "global"
    }
}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# --- RIG-GOM-023 : Deepwater GOM ---
<new_policy = {
    "wits" : {
        "name"      : "RIG-GOM-023",
        "uns_layer" : "rig",
        "namespace" : "wits/record01/RIG-GOM-023",
        "parent"    : "wits/record01",
        "rig_id"    : "RIG-GOM-023",
        "rig_name"  : "Deepwater",
        "region"    : "Gulf of Mexico",
        "location"  : "offshore",
        "dbms"      : "timbergrove",
        "table"     : "rig_data",
        "where"     : "rig_id='RIG-GOM-023'",
        "ledger"    : "global"
    }
}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ---------------------------------------------------------------------------
# LAYER 4: Tag Policies — generated for each rig x each column
# Column → WITS Record 1 Item mapping:
#   timestamp         → item 1  (Date and Time,          UTC)
#   rig_id            → item 2  (Rig Identifier,          -)
#   activity          → item 4  (Activity Code,           -)
#   status            → item 5  (Rig Status,              -)
#   bit_depth         → item 7  (Bit Measured Depth,      ft)
#   measured_depth    → item 8  (Hole Measured Depth,     ft)
#   true_vertical_depth→item 9  (Hole True Vertical Depth,ft)
#   rop               → item 10 (Rate of Penetration,     ft/hr)
#   block_height      → item 11 (Block Height,            ft)
#   hookload          → item 12 (Hookload,                klbf)
#   wob               → item 14 (Weight on Bit,           klbf)
#   hole_depth        → item 15 (Hole Depth,              ft)
#   rpm               → item 16 (Rotary Speed,            rpm)
#   torque            → item 17 (Rotary Torque,           ft-klbf)
#   standpipe_pressure→ item 18 (Standpipe Pressure,      psi)
#   choke_pressure    → item 20 (Choke Pressure,          psi)
#   flow_rate         → item 24 (Pump Output Flow Rate,   gal/min)
#   mud_weight_in     → item 27 (Mud Weight In,           g/cc)
#   mud_weight_out    → item 28 (Mud Weight Out,          g/cc)
#   mud_temp_in       → item 29 (Mud Temperature In,      degF)
#   mud_temp_out      → item 30 (Mud Temperature Out,     degF)
#   total_gas         → item 38 (Total Gas,               %)
# ---------------------------------------------------------------------------

# ============================================================
# RIG-TX-001 Tags
# ============================================================

<new_policy = {"wits":{"name":"RIG-TX-001/timestamp","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/timestamp","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"timestamp","wits_record":"1","wits_item":"1","wits_description":"Date and Time","data_type":"timestamp","unit":"UTC","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/rig_id","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/rig_id","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"rig_id","wits_record":"1","wits_item":"2","wits_description":"Rig Identifier","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/activity","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/activity","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"activity","wits_record":"1","wits_item":"4","wits_description":"Activity Code","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/status","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/status","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"status","wits_record":"1","wits_item":"5","wits_description":"Rig Status","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/bit_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/bit_depth","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"bit_depth","wits_record":"1","wits_item":"7","wits_description":"Bit Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/measured_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/measured_depth","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"measured_depth","wits_record":"1","wits_item":"8","wits_description":"Hole Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/true_vertical_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/true_vertical_depth","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"true_vertical_depth","wits_record":"1","wits_item":"9","wits_description":"Hole True Vertical Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/rop","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/rop","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"rop","wits_record":"1","wits_item":"10","wits_description":"Rate of Penetration","data_type":"float","unit":"ft/hr","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/block_height","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/block_height","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"block_height","wits_record":"1","wits_item":"11","wits_description":"Block Height","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/hookload","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/hookload","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"hookload","wits_record":"1","wits_item":"12","wits_description":"Hookload","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/wob","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/wob","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"wob","wits_record":"1","wits_item":"14","wits_description":"Weight on Bit","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/hole_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/hole_depth","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"hole_depth","wits_record":"1","wits_item":"15","wits_description":"Hole Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/rpm","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/rpm","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"rpm","wits_record":"1","wits_item":"16","wits_description":"Rotary Speed","data_type":"float","unit":"rpm","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/torque","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/torque","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"torque","wits_record":"1","wits_item":"17","wits_description":"Rotary Torque","data_type":"float","unit":"ft-klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/standpipe_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/standpipe_pressure","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"standpipe_pressure","wits_record":"1","wits_item":"18","wits_description":"Standpipe Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/choke_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/choke_pressure","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"choke_pressure","wits_record":"1","wits_item":"20","wits_description":"Choke Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/flow_rate","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/flow_rate","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"flow_rate","wits_record":"1","wits_item":"24","wits_description":"Pump Output Flow Rate","data_type":"float","unit":"gal/min","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/mud_weight_in","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/mud_weight_in","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"mud_weight_in","wits_record":"1","wits_item":"27","wits_description":"Mud Weight In","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/mud_weight_out","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/mud_weight_out","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"mud_weight_out","wits_record":"1","wits_item":"28","wits_description":"Mud Weight Out","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/mud_temp_in","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/mud_temp_in","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"mud_temp_in","wits_record":"1","wits_item":"29","wits_description":"Mud Temperature In","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/mud_temp_out","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/mud_temp_out","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"mud_temp_out","wits_record":"1","wits_item":"30","wits_description":"Mud Temperature Out","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-001/total_gas","uns_layer":"tag","namespace":"wits/record01/RIG-TX-001/total_gas","parent":"wits/record01/RIG-TX-001","rig_id":"RIG-TX-001","column":"total_gas","wits_record":"1","wits_item":"38","wits_description":"Total Gas","data_type":"float","unit":"%","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-001'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ============================================================
# RIG-TX-007 Tags
# ============================================================

<new_policy = {"wits":{"name":"RIG-TX-007/timestamp","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/timestamp","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"timestamp","wits_record":"1","wits_item":"1","wits_description":"Date and Time","data_type":"timestamp","unit":"UTC","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/rig_id","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/rig_id","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"rig_id","wits_record":"1","wits_item":"2","wits_description":"Rig Identifier","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/activity","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/activity","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"activity","wits_record":"1","wits_item":"4","wits_description":"Activity Code","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/status","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/status","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"status","wits_record":"1","wits_item":"5","wits_description":"Rig Status","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/bit_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/bit_depth","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"bit_depth","wits_record":"1","wits_item":"7","wits_description":"Bit Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/measured_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/measured_depth","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"measured_depth","wits_record":"1","wits_item":"8","wits_description":"Hole Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/true_vertical_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/true_vertical_depth","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"true_vertical_depth","wits_record":"1","wits_item":"9","wits_description":"Hole True Vertical Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/rop","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/rop","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"rop","wits_record":"1","wits_item":"10","wits_description":"Rate of Penetration","data_type":"float","unit":"ft/hr","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/block_height","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/block_height","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"block_height","wits_record":"1","wits_item":"11","wits_description":"Block Height","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/hookload","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/hookload","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"hookload","wits_record":"1","wits_item":"12","wits_description":"Hookload","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/wob","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/wob","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"wob","wits_record":"1","wits_item":"14","wits_description":"Weight on Bit","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/hole_depth","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/hole_depth","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"hole_depth","wits_record":"1","wits_item":"15","wits_description":"Hole Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/rpm","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/rpm","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"rpm","wits_record":"1","wits_item":"16","wits_description":"Rotary Speed","data_type":"float","unit":"rpm","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/torque","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/torque","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"torque","wits_record":"1","wits_item":"17","wits_description":"Rotary Torque","data_type":"float","unit":"ft-klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/standpipe_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/standpipe_pressure","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"standpipe_pressure","wits_record":"1","wits_item":"18","wits_description":"Standpipe Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/choke_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/choke_pressure","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"choke_pressure","wits_record":"1","wits_item":"20","wits_description":"Choke Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/flow_rate","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/flow_rate","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"flow_rate","wits_record":"1","wits_item":"24","wits_description":"Pump Output Flow Rate","data_type":"float","unit":"gal/min","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/mud_weight_in","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/mud_weight_in","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"mud_weight_in","wits_record":"1","wits_item":"27","wits_description":"Mud Weight In","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/mud_weight_out","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/mud_weight_out","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"mud_weight_out","wits_record":"1","wits_item":"28","wits_description":"Mud Weight Out","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/mud_temp_in","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/mud_temp_in","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"mud_temp_in","wits_record":"1","wits_item":"29","wits_description":"Mud Temperature In","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/mud_temp_out","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/mud_temp_out","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"mud_temp_out","wits_record":"1","wits_item":"30","wits_description":"Mud Temperature Out","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-TX-007/total_gas","uns_layer":"tag","namespace":"wits/record01/RIG-TX-007/total_gas","parent":"wits/record01/RIG-TX-007","rig_id":"RIG-TX-007","column":"total_gas","wits_record":"1","wits_item":"38","wits_description":"Total Gas","data_type":"float","unit":"%","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-TX-007'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ============================================================
# RIG-ND-012 Tags
# ============================================================

<new_policy = {"wits":{"name":"RIG-ND-012/timestamp","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/timestamp","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"timestamp","wits_record":"1","wits_item":"1","wits_description":"Date and Time","data_type":"timestamp","unit":"UTC","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/rig_id","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/rig_id","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"rig_id","wits_record":"1","wits_item":"2","wits_description":"Rig Identifier","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/activity","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/activity","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"activity","wits_record":"1","wits_item":"4","wits_description":"Activity Code","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/status","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/status","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"status","wits_record":"1","wits_item":"5","wits_description":"Rig Status","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/bit_depth","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/bit_depth","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"bit_depth","wits_record":"1","wits_item":"7","wits_description":"Bit Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/measured_depth","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/measured_depth","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"measured_depth","wits_record":"1","wits_item":"8","wits_description":"Hole Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/true_vertical_depth","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/true_vertical_depth","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"true_vertical_depth","wits_record":"1","wits_item":"9","wits_description":"Hole True Vertical Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/rop","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/rop","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"rop","wits_record":"1","wits_item":"10","wits_description":"Rate of Penetration","data_type":"float","unit":"ft/hr","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/block_height","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/block_height","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"block_height","wits_record":"1","wits_item":"11","wits_description":"Block Height","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/hookload","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/hookload","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"hookload","wits_record":"1","wits_item":"12","wits_description":"Hookload","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/wob","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/wob","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"wob","wits_record":"1","wits_item":"14","wits_description":"Weight on Bit","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/hole_depth","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/hole_depth","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"hole_depth","wits_record":"1","wits_item":"15","wits_description":"Hole Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/rpm","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/rpm","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"rpm","wits_record":"1","wits_item":"16","wits_description":"Rotary Speed","data_type":"float","unit":"rpm","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/torque","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/torque","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"torque","wits_record":"1","wits_item":"17","wits_description":"Rotary Torque","data_type":"float","unit":"ft-klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/standpipe_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/standpipe_pressure","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"standpipe_pressure","wits_record":"1","wits_item":"18","wits_description":"Standpipe Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/choke_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/choke_pressure","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"choke_pressure","wits_record":"1","wits_item":"20","wits_description":"Choke Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/flow_rate","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/flow_rate","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"flow_rate","wits_record":"1","wits_item":"24","wits_description":"Pump Output Flow Rate","data_type":"float","unit":"gal/min","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/mud_weight_in","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/mud_weight_in","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"mud_weight_in","wits_record":"1","wits_item":"27","wits_description":"Mud Weight In","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/mud_weight_out","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/mud_weight_out","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"mud_weight_out","wits_record":"1","wits_item":"28","wits_description":"Mud Weight Out","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/mud_temp_in","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/mud_temp_in","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"mud_temp_in","wits_record":"1","wits_item":"29","wits_description":"Mud Temperature In","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/mud_temp_out","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/mud_temp_out","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"mud_temp_out","wits_record":"1","wits_item":"30","wits_description":"Mud Temperature Out","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-ND-012/total_gas","uns_layer":"tag","namespace":"wits/record01/RIG-ND-012/total_gas","parent":"wits/record01/RIG-ND-012","rig_id":"RIG-ND-012","column":"total_gas","wits_record":"1","wits_item":"38","wits_description":"Total Gas","data_type":"float","unit":"%","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-ND-012'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ============================================================
# RIG-GOM-023 Tags
# ============================================================

<new_policy = {"wits":{"name":"RIG-GOM-023/timestamp","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/timestamp","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"timestamp","wits_record":"1","wits_item":"1","wits_description":"Date and Time","data_type":"timestamp","unit":"UTC","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/rig_id","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/rig_id","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"rig_id","wits_record":"1","wits_item":"2","wits_description":"Rig Identifier","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/activity","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/activity","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"activity","wits_record":"1","wits_item":"4","wits_description":"Activity Code","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/status","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/status","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"status","wits_record":"1","wits_item":"5","wits_description":"Rig Status","data_type":"string","unit":"","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/bit_depth","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/bit_depth","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"bit_depth","wits_record":"1","wits_item":"7","wits_description":"Bit Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/measured_depth","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/measured_depth","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"measured_depth","wits_record":"1","wits_item":"8","wits_description":"Hole Measured Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/true_vertical_depth","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/true_vertical_depth","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"true_vertical_depth","wits_record":"1","wits_item":"9","wits_description":"Hole True Vertical Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/rop","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/rop","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"rop","wits_record":"1","wits_item":"10","wits_description":"Rate of Penetration","data_type":"float","unit":"ft/hr","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/block_height","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/block_height","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"block_height","wits_record":"1","wits_item":"11","wits_description":"Block Height","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/hookload","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/hookload","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"hookload","wits_record":"1","wits_item":"12","wits_description":"Hookload","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/wob","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/wob","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"wob","wits_record":"1","wits_item":"14","wits_description":"Weight on Bit","data_type":"float","unit":"klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/hole_depth","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/hole_depth","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"hole_depth","wits_record":"1","wits_item":"15","wits_description":"Hole Depth","data_type":"float","unit":"ft","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/rpm","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/rpm","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"rpm","wits_record":"1","wits_item":"16","wits_description":"Rotary Speed","data_type":"float","unit":"rpm","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/torque","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/torque","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"torque","wits_record":"1","wits_item":"17","wits_description":"Rotary Torque","data_type":"float","unit":"ft-klbf","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/standpipe_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/standpipe_pressure","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"standpipe_pressure","wits_record":"1","wits_item":"18","wits_description":"Standpipe Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/choke_pressure","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/choke_pressure","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"choke_pressure","wits_record":"1","wits_item":"20","wits_description":"Choke Pressure","data_type":"float","unit":"psi","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/flow_rate","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/flow_rate","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"flow_rate","wits_record":"1","wits_item":"24","wits_description":"Pump Output Flow Rate","data_type":"float","unit":"gal/min","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/mud_weight_in","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/mud_weight_in","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"mud_weight_in","wits_record":"1","wits_item":"27","wits_description":"Mud Weight In","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/mud_weight_out","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/mud_weight_out","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"mud_weight_out","wits_record":"1","wits_item":"28","wits_description":"Mud Weight Out","data_type":"float","unit":"g/cc","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/mud_temp_in","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/mud_temp_in","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"mud_temp_in","wits_record":"1","wits_item":"29","wits_description":"Mud Temperature In","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/mud_temp_out","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/mud_temp_out","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"mud_temp_out","wits_record":"1","wits_item":"30","wits_description":"Mud Temperature Out","data_type":"float","unit":"degF","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

<new_policy = {"wits":{"name":"RIG-GOM-023/total_gas","uns_layer":"tag","namespace":"wits/record01/RIG-GOM-023/total_gas","parent":"wits/record01/RIG-GOM-023","rig_id":"RIG-GOM-023","column":"total_gas","wits_record":"1","wits_item":"38","wits_description":"Total Gas","data_type":"float","unit":"%","dbms":"timbergrove","table":"rig_data","where":"rig_id='RIG-GOM-023'","ledger":"global"}}>
blockchain prepare policy !new_policy
blockchain insert where policy = !new_policy and local = true and master = !master_node

# ---------------------------------------------------------------------------
# Verify load - query all wits policies from blockchain
# ---------------------------------------------------------------------------
blockchain get wits
