from flask import Flask, request
import requests
import jwt
import json 
from datetime import datetime, timedelta
import os

app = Flask(__name__)

# GitHub App credentials
GITHUB_APP_ID = "1743871"  # 
GITHUB_PRIVATE_KEY = """"""  #

pending_deployments = {}

def save_webhook_payload(event_type, data):
    """Save webhook payload to JSON file for review"""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"webhook_{event_type}_{timestamp}.json"
    
    # Create webhooks directory if it doesn't exist
    os.makedirs("webhook_logs", exist_ok=True)
    
    filepath = os.path.join("webhook_logs", filename)
    
    # Add metadata
    payload_with_meta = {
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "headers": dict(request.headers),
        "payload": data
    }
    
    try:
        with open(filepath, 'w') as f:
            json.dump(payload_with_meta, f, indent=2)
        print(f"💾 Saved payload: {filepath}")
        return filepath
    except Exception as e:
        print(f" Failed to save payload: {e}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    event_type = request.headers.get('X-Github-Event', 'unknown')
    
    print("=" * 80)
    print(f"WEBHOOK RECEIVED: {event_type}")
    print(f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)
    
    # Save ALL webhook payloads to files
    saved_file = save_webhook_payload(event_type, data)
    
    # Show basic info for any webhook
    if data:
        print(f" Basic Info:")
        if 'repository' in data:
            repo_name = data['repository']['full_name']
            print(f"   Repository: {repo_name}")
        if 'action' in data:
            print(f"   Action: {data['action']}")
        if 'sender' in data:
            print(f"   Sender: {data['sender']['login']}")
    
    # Only process deployment_protection_rule events  
    if event_type != 'deployment_protection_rule':
        print(f" Ignoring {event_type} event (saved to {saved_file})")
        print("=" * 80)
        return "OK"
    
    print(f"Processing deployment_protection_rule event...")
    
    # Extract data
    repo_owner = data['repository']['owner']['login']
    repo_name = data['repository']['name'] 
    installation_id = data['installation']['id']
    callback_url = data['deployment_callback_url']
    environment = data['environment']
    
    deployment_key = f"{repo_owner}/{repo_name}/{environment}"
    
    print(f"NEW DEPLOYMENT: {deployment_key}")
    
    # Store deployment info
    pending_deployments[deployment_key] = {
        'callback_url': callback_url,
        'installation_id': installation_id,
        'environment': environment,
        'repo_owner': repo_owner,
        'repo_name': repo_name,
        'received_at': datetime.utcnow().strftime('%H:%M:%S'),
        'payload_file': saved_file  # Reference to saved payload
    }
    
    # Send initial waiting message
    token = get_installation_token(installation_id)
    if token:
        payload = {
            'environment_name': environment,
            'comment': f'A rollout is in progress. The pipeline will resume when it completes...'
        }
        
        response = requests.post(callback_url, headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'
        }, json=payload)
        
        print(f" Wait message sent: {response.status_code}")
        if response.status_code != 204:
            print(f"   Response: {response.text}")
    
    print(f"\n PENDING DEPLOYMENTS ({len(pending_deployments)}):")
    for key, dep in pending_deployments.items():
        print(f"   • {key} ({dep['received_at']}) - {dep['payload_file']}")
    
    print(f"\n COMMANDS:")
    print(f"   Approve: curl -X POST http://localhost:5000/approve/{deployment_key}")
    print(f"   Reject:  curl -X POST http://localhost:5000/reject/{deployment_key}")
    print(f"   List:    curl http://localhost:5000/pending")
    print("=" * 80)
    
    return "OK"

@app.route('/approve/<path:deployment_key>', methods=['POST'])
def approve_deployment(deployment_key):
    if deployment_key not in pending_deployments:
        return jsonify({'error': 'Deployment not found'}), 404
    
    deployment = pending_deployments[deployment_key]
    token = get_installation_token(deployment['installation_id'])
    
    if not token:
        return jsonify({'error': 'Failed to get GitHub token'}), 500
    
    payload = {
        'environment_name': deployment['environment'],
        'state': 'approved',
        'comment': f' Rollout completed successfully! Pipeline can proceed.'
    }
    
    response = requests.post(deployment['callback_url'], headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
    }, json=payload)
    
    if response.status_code == 204:
        del pending_deployments[deployment_key]
        print(f"\n APPROVED: {deployment_key}")
        print(f" Remaining: {len(pending_deployments)} pending")
        return jsonify({'status': 'approved'})
    else:
        print(f" Approval failed: {response.status_code} - {response.text}")
        return jsonify({'error': 'Failed to approve'}), 500

@app.route('/reject/<path:deployment_key>', methods=['POST'])
def reject_deployment(deployment_key):
    if deployment_key not in pending_deployments:
        return jsonify({'error': 'Deployment not found'}), 404
    
    deployment = pending_deployments[deployment_key]
    token = get_installation_token(deployment['installation_id'])
    
    if not token:
        return jsonify({'error': 'Failed to get GitHub token'}), 500
    
    payload = {
        'environment_name': deployment['environment'],
        'state': 'rejected',
        'comment': f' Rollout failed! Deployment blocked at {datetime.utcnow().strftime("%H:%M:%S")}'
    }
    
    response = requests.post(deployment['callback_url'], headers={
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
    }, json=payload)
    
    if response.status_code == 204:
        del pending_deployments[deployment_key]
        print(f"\n REJECTED: {deployment_key}")
        print(f"📋 Remaining: {len(pending_deployments)} pending")
        return jsonify({'status': 'rejected'})
    else:
        print(f" Rejection failed: {response.status_code} - {response.text}")
        return jsonify({'error': 'Failed to reject'}), 500

@app.route('/pending')
def list_pending():
    """List pending deployments"""
    print(f"\n PENDING DEPLOYMENTS ({len(pending_deployments)}):")
    for key, dep in pending_deployments.items():
        print(f"   • {key} (received at {dep['received_at']})")
        print(f"     Payload: {dep['payload_file']}")
        print(f"     Approve: curl -X POST http://localhost:5000/approve/{key}")
        print(f"     Reject:  curl -X POST http://localhost:5000/reject/{key}")
        print()
    
    if not pending_deployments:
        print("   (none)")
    
    return jsonify({
        'count': len(pending_deployments), 
        'deployments': [
            {
                'key': key,
                'environment': dep['environment'],
                'received_at': dep['received_at'],
                'payload_file': dep['payload_file']
            }
            for key, dep in pending_deployments.items()
        ]
    })

@app.route('/payloads')
def list_payloads():
    """List all saved webhook payloads"""
    try:
        webhook_dir = "webhook_logs"
        if not os.path.exists(webhook_dir):
            return jsonify({'payloads': [], 'count': 0})
        
        files = [f for f in os.listdir(webhook_dir) if f.endswith('.json')]
        files.sort(reverse=True)  # Newest first
        
        payload_info = []
        for filename in files:
            filepath = os.path.join(webhook_dir, filename)
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                payload_info.append({
                    'filename': filename,
                    'timestamp': data.get('timestamp'),
                    'event_type': data.get('event_type'),
                    'size_kb': round(os.path.getsize(filepath) / 1024, 1)
                })
            except Exception as e:
                payload_info.append({
                    'filename': filename,
                    'error': str(e)
                })
        
        print(f"\n WEBHOOK PAYLOADS ({len(payload_info)}):")
        for info in payload_info:
            if 'error' not in info:
                print(f"   • {info['filename']} ({info['event_type']}) - {info['size_kb']}KB")
            else:
                print(f"   • {info['filename']} (ERROR: {info['error']})")
        
        return jsonify({'payloads': payload_info, 'count': len(payload_info)})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_installation_token(installation_id):
    try:
        now = datetime.utcnow()
        payload = {
            'iat': now,
            'exp': now + timedelta(minutes=10),
            'iss': GITHUB_APP_ID
        }
        
        jwt_token = jwt.encode(payload, GITHUB_PRIVATE_KEY, algorithm='RS256')
        
        response = requests.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                'Authorization': f'Bearer {jwt_token}',
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'
            },
            json={'permissions': {'deployments': 'write'}}
        )
        
        if response.status_code != 201:
            print(f" Token request failed: {response.status_code} - {response.text}")
            return None
            
        return response.json()['token']
        
    except Exception as e:
        print(f" Token error: {e}")
        return None

if __name__ == '__main__':
    print(" GitHub Deployment Control (Terminal Mode)")
    print("Webhook payloads will be saved to: webhook_logs/")
    print("Commands:")
    print("   List pending:  curl http://localhost:5000/pending")
    print("   List payloads: curl http://localhost:5000/payloads")
    print("-" * 80)
    
    # Create logs directory
    os.makedirs("webhook_logs", exist_ok=True)
    
    app.run(host='0.0.0.0', port=5000, debug=True)
