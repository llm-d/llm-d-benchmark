#!/bin/bash

# Helper function to run kubectl commands and capture output, with command echoing
run_kubectl_command() {
    local command=$1
    echo "Running command: kubectl $command"  # Echo the command for troubleshooting
    kubectl $command -o yaml || { echo "Error executing command: kubectl $command"; exit 1; }  # Exit on error
}

# Function to fetch RoleBindings and ClusterRoleBindings
get_rolebindings() {
    run_kubectl_command "get rolebindings --all-namespaces"
}

get_clusterrolebindings() {
    run_kubectl_command "get clusterrolebindings"
}

# Function to get the details of a Role or ClusterRole
get_role_details() {
    local role_name=$1
    local namespace=$2
    if [[ -n "$role_name" && "$role_name" != "null" && "$role_name" != "N/A" ]]; then
        if [[ -n "$namespace" && "$namespace" != "null" ]]; then
            run_kubectl_command "get role $role_name -n $namespace"
        else
            run_kubectl_command "get clusterrole $role_name"
        fi
    else
        echo "Invalid role reference: $role_name, skipping."
    fi
}

# Function to fetch all ServiceAccounts
get_serviceaccounts() {
    run_kubectl_command "get serviceaccounts --all-namespaces"
}

# Function to analyze RoleBindings and ClusterRoleBindings for potential privilege escalation
analyze_roles() {
    rolebindings_yaml=$(get_rolebindings)
    clusterrolebindings_yaml=$(get_clusterrolebindings)

    # Debugging: Print the raw YAML for RoleBindings and ClusterRoleBindings
    echo "Debugging: RoleBindings YAML"
    echo "$rolebindings_yaml"
    echo "Debugging: ClusterRoleBindings YAML"
    echo "$clusterrolebindings_yaml"

    report="## 1. RoleBindings and ClusterRoleBindings\n"
    
    # Iterate through RoleBindings
    echo "$rolebindings_yaml" | yq eval '.items[]' - | while read -r rb; do
        role_ref=$(echo "$rb" | yq eval '.roleRef.name' -)
        subjects=$(echo "$rb" | yq eval '.subjects[].name' -)
        namespace=$(echo "$rb" | yq eval '.metadata.namespace' -)

        if [[ "$namespace" == "null" || -z "$namespace" ]]; then
            namespace="N/A"
        fi

        report+="### RoleBinding: $(echo "$rb" | yq eval '.metadata.name' -)\n"
        report+="Assigned to: $subjects\n"
        report+="Role: $role_ref\n"
        report+="Namespace: $namespace\n"

        role_details=$(get_role_details "$role_ref" "$namespace")

        resources=$(echo "$role_details" | yq eval '.rules[].resources' -)
        verbs=$(echo "$role_details" | yq eval '.rules[].verbs' -)

        report+="  - Resources: $resources\n"
        report+="  - Verbs: $verbs\n"

        # Check for wildcard permissions
        if echo "$resources" | grep -q "\*"; then
            report+="  - Warning: Wildcard permissions detected.\n"
        fi
    done

    # Iterate through ClusterRoleBindings
    echo "$clusterrolebindings_yaml" | yq eval '.items[]' - | while read -r crb; do
        clusterrole_ref=$(echo "$crb" | yq eval '.roleRef.name' -)
        subjects=$(echo "$crb" | yq eval '.subjects[].name' -)

        report+="### ClusterRoleBinding: $(echo "$crb" | yq eval '.metadata.name' -)\n"
        report+="Assigned to: $subjects\n"
        report+="ClusterRole: $clusterrole_ref\n"

        clusterrole_details=$(get_role_details "$clusterrole_ref" "")

        resources=$(echo "$clusterrole_details" | yq eval '.rules[].resources' -)
        verbs=$(echo "$clusterrole_details" | yq eval '.rules[].verbs' -)

        report+="  - Resources: $resources\n"
        report+="  - Verbs: $verbs\n"

        # Check for wildcard permissions
        if echo "$resources" | grep -q "\*"; then
            report+="  - Warning: Wildcard permissions detected.\n"
        fi
    done

    echo -e "$report"
}

# Function to analyze ServiceAccount permissions
analyze_serviceaccounts() {
    serviceaccounts_yaml=$(get_serviceaccounts)

    sa_report="## 2. ServiceAccount Permissions\n"

    # Iterate through ServiceAccounts
    echo "$serviceaccounts_yaml" | yq eval '.items[]' - | while read -r sa; do
        namespace=$(echo "$sa" | yq eval '.metadata.namespace' -)
        name=$(echo "$sa" | yq eval '.metadata.name' -)
        
        associated_roles=""

        # Check for associated RoleBindings and ClusterRoleBindings for the service account
        rolebindings=$(get_rolebindings)
        clusterrolebindings=$(get_clusterrolebindings)

        # Check RoleBindings
        echo "$rolebindings" | yq eval '.items[]' - | while read -r rb; do
            subjects=$(echo "$rb" | yq eval '.subjects[].name' -)
            if echo "$subjects" | grep -q "$name"; then
                role_name=$(echo "$rb" | yq eval '.roleRef.name' -)
                associated_roles+="$role_name, "
            fi
        done

        # Check ClusterRoleBindings
        echo "$clusterrolebindings" | yq eval '.items[]' - | while read -r crb; do
            subjects=$(echo "$crb" | yq eval '.subjects[].name' -)
            if echo "$subjects" | grep -q "$name"; then
                clusterrole_name=$(echo "$crb" | yq eval '.roleRef.name' -)
                associated_roles+="$clusterrole_name, "
            fi
        done

        if [[ -n "$associated_roles" ]]; then
            sa_report+="### ServiceAccount: $name (Namespace: $namespace)\n"
            sa_report+="Assigned Roles: ${associated_roles%, }\n"
        fi
    done

    echo -e "$sa_report"
}

# Main function to generate the audit report
generate_report() {
    echo "# RBAC Audit Report for Kubernetes Cluster"

    role_report=$(analyze_roles)
    sa_report=$(analyze_serviceaccounts)

    echo -e "$role_report"
    echo -e "$sa_report"
}

# Generate the report and save it to a file
generate_report > rbac_audit_report.md
echo "RBAC audit report has been generated as 'rbac_audit_report.md'."

