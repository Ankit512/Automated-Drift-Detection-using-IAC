import logging
import json
import os
import sys

# Azure libraries
try:
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.resource import ResourceManagementClient
    from azure.core.exceptions import ResourceNotFoundError, ClientAuthenticationError
except ImportError:
    print("CRITICAL ERROR: Missing Azure libraries.")
    print("Please run: pip install azure-identity azure-mgmt-resource")
    sys.exit(1)

# Configure logging to print to console
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# ### CONFIGURATION ###
CONFIG = {
    # Replace with your actual Subscription ID
    "SUBSCRIPTION_ID": "YOUR-SUBSCRIPTION-ID-HERE", 
    
    # "westeurope" (Netherlands) is the standard recommended region
    "TARGET_REGION": "westeurope",  
    
    "RESOURCE_GROUPS": [
        "rg-production-app-001",
        "rg-production-db-001"
    ],
    
    # Tag Compliance Rules
    "EXPECTED_TAGS": {
        "Environment": "Production"
    }
}
# #####################

def check_azure_drift():
    logger.info('--- Starting Azure Drift Check ---')

    # 1. Authenticate
    # DefaultAzureCredential will look for: Environment Vars -> Managed Identity -> VS Code -> Azure CLI
    try:
        credential = DefaultAzureCredential()
        subscription_id = CONFIG["SUBSCRIPTION_ID"]
        resource_client = ResourceManagementClient(credential, subscription_id)
    except Exception as e:
        logger.error(f"Authentication Failed. Run 'az login' in your terminal. Error: {e}")
        return

    drift_report = []
    
    # 2. Iterate through Resource Groups
    for rg_name in CONFIG["RESOURCE_GROUPS"]:
        logger.info(f"Checking Resource Group: {rg_name}")
        
        try:
            # Check 1: Does RG exist?
            try:
                rg = resource_client.resource_groups.get(rg_name)
            except ResourceNotFoundError:
                logger.error(f"Resource Group '{rg_name}' NOT FOUND.")
                drift_report.append({"ResourceGroup": rg_name, "Status": "MISSING"})
                continue

            # Check 2: Provisioning State
            if rg.provisioning_state != "Succeeded":
                 drift_report.append({
                    "ResourceGroup": rg_name,
                    "Status": "UNHEALTHY",
                    "Reason": f"State is {rg.provisioning_state}"
                })

            # Check 3: Resource Drift (Region & Tags)
            resources = resource_client.resources.list_by_resource_group(rg_name)
            
            for resource in resources:
                resource_drifted = False
                drift_reasons = []

                # A. Region Check
                if resource.location != CONFIG["TARGET_REGION"]:
                    resource_drifted = True
                    drift_reasons.append(f"Region: {resource.location} (Expected {CONFIG['TARGET_REGION']})")

                # B. Tag Check
                current_tags = resource.tags or {}
                for key, value in CONFIG["EXPECTED_TAGS"].items():
                    if current_tags.get(key) != value:
                        resource_drifted = True
                        drift_reasons.append(f"Missing Tag: {key}={value}")

                if resource_drifted:
                    drift_report.append({
                        "Resource": resource.name,
                        "Type": resource.type,
                        "Status": "DRIFTED",
                        "Details": ", ".join(drift_reasons)
                    })

        except ClientAuthenticationError:
            logger.error("Authentication rejected. Check your Subscription ID.")
            break
        except Exception as e:
            logger.error(f"Unexpected error checking {rg_name}: {str(e)}")

    # 3. Report Results
    if drift_report:
        print("\n" + "="*30)
        print("⚠️  DRIFT DETECTED REPORT")
        print("="*30)
        print(json.dumps(drift_report, indent=2))
    else:
        print("\n✅ All checked resources are IN_SYNC and Compliant.")

# This block allows you to run it locally
if __name__ == "__main__":
    check_azure_drift()
