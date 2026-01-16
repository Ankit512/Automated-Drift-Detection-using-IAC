import boto3
import json
import logging
import time

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ### [MODIFIED] Configuration Block
# Instead of Environment Variables, I moved the configuration here so it works 
# without external setup. You can edit the list below directly.
CONFIG = {
    "SNS_TOPIC_ARN": "arn:aws:sns:ap-south-1:471112828084:Detect_Drift",
    "STACK_ARNS": [
        "arn:aws:cloudformation:ap-south-1:471112828084:stack/DynamoDB/eac28c70-f209-11ee-8b09-0a02e01a9aa3",
        "arn:aws:cloudformation:ap-south-1:471112828084:stack/EC2instance/74c0e490-f209-11ee-a0af-0aac48631e59",
        "arn:aws:cloudformation:ap-south-1:471112828084:stack/S3Bucket/edefa5f0-f208-11ee-bd3f-06859283fa06"
    ]
}

def lambda_handler(event, context):
    cf_client = boto3.client('cloudformation')
    sns_client = boto3.client('sns')

    # Load config from the dictionary above
    stack_names = CONFIG['STACK_ARNS']
    sns_topic_arn = CONFIG['SNS_TOPIC_ARN']
    
    results = []

    # ### [ADDED] Loop Error Handling
    # I added a try-except block inside the loop. In the original script, if one stack 
    # failed (e.g., permissions error), the whole script crashed. Now it skips to the next stack.
    for stack_name in stack_names:
        try:
            logger.info(f"--- Processing stack: {stack_name} ---")

            # Step 1: Trigger Drift Detection
            detect_resp = cf_client.detect_stack_drift(StackName=stack_name)
            drift_detection_id = detect_resp['StackDriftDetectionId']
            
            logger.info(f"Drift detection initiated. ID: {drift_detection_id}")

            # ### [FIXED] Polling Logic (Replaced hard sleep)
            # The original script used `time.sleep(30)` which wastes time if the check finishes in 2 seconds.
            # I replaced it with a loop that checks status every 2 seconds.
            stack_drift_status = None
            detection_status = None
            
            max_retries = 20
            for attempt in range(max_retries):
                status_resp = cf_client.describe_stack_drift_detection_status(
                    StackDriftDetectionId=drift_detection_id
                )
                detection_status = status_resp['DetectionStatus']
                
                # ### [FIXED] Logic Bug
                # Originally, you were overwriting `drift_status` repeatedly. 
                # I separated `detection_status` (is the check done?) from `stack_drift_status` (did it drift?).
                if detection_status == 'DETECTION_COMPLETE':
                    stack_drift_status = status_resp['StackDriftStatus']
                    logger.info(f"Detection complete. Stack Status: {stack_drift_status}")
                    break
                elif detection_status == 'DETECTION_FAILED':
                    logger.error(f"Detection failed for stack {stack_name}: {status_resp.get('DetectionStatusReason')}")
                    break
                
                time.sleep(2) 
            else:
                # If loop finishes without breaking, it timed out
                logger.warning(f"Timeout waiting for drift detection on stack: {stack_name}")
                continue 

            if detection_status != 'DETECTION_COMPLETE':
                continue

            # Step 3: Fetch Drift Details
            drift_details = []
            
            # ### [ADDED] Optimization
            # We only fetch resource details if the stack actually shows 'DRIFTED'.
            # If it is 'IN_SYNC', there is no need to query for resource details.
            if stack_drift_status == 'DRIFTED':
                
                # ### [ADDED] Pagination Support
                # The original script missed resources if a stack had many items (AWS returns results in pages).
                # I added a Paginator to ensure we catch every single drifted resource.
                paginator = cf_client.get_paginator('describe_stack_resource_drifts')
                
                # ### [IMPROVED] Filtering
                # I added `StackResourceDriftStatusFilters`. We only care about MODIFIED or DELETED items.
                page_iterator = paginator.paginate(
                    StackName=stack_name,
                    StackResourceDriftStatusFilters=['MODIFIED', 'DELETED']
                )

                for page in page_iterator:
                    for drift in page['StackResourceDrifts']:
                        drift_details.append({
                            'Resource': drift['LogicalResourceId'],
                            'Type': drift['ResourceType'],
                            'Status': drift['StackResourceDriftStatus'],
                            # ### [ADDED] Safe Access
                            # Used .get() to avoid crashes if expected/actual properties are missing
                            'Expected': drift.get('ExpectedProperties', 'N/A'),
                            'Actual': drift.get('ActualProperties', 'N/A')
                        })

            # Step 4: Prepare Notification
            # Clean stack name (removes the long ARN path)
            clean_stack_name = stack_name.split('/')[-2] if '/' in stack_name else stack_name
            
            # ### [MODIFIED] Notification Logic
            # I added a check so we ONLY send an SNS email if Drift is detected.
            # This prevents "Everything is fine" spam emails.
            if stack_drift_status == 'DRIFTED':
                message_body = (
                    f"⚠️ DRIFT DETECTED\n\n"
                    f"Stack: {clean_stack_name}\n"
                    f"Status: {stack_drift_status}\n\n"
                    f"Drifted Resources ({len(drift_details)}):\n"
                    f"{json.dumps(drift_details, indent=2, default=str)}"
                )
                
                sns_client.publish(
                    TopicArn=sns_topic_arn,
                    Message=message_body,
                    Subject=f"Drift Report: {clean_stack_name} [DRIFTED]"
                )
            else:
                logger.info(f"Stack {clean_stack_name} is IN_SYNC. No notification sent.")
            
            results.append(f"{clean_stack_name}: {stack_drift_status}")

        except Exception as e:
            # Catches unexpected errors for this specific stack so the loop can continue to the next one
            logger.error(f"Error processing stack {stack_name}: {str(e)}")
            continue

    return {
        'statusCode': 200,
        'body': json.dumps(f"Process complete. Results: {results}")
    }
