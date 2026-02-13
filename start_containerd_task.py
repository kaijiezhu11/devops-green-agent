#!/usr/bin/env python3
"""
Start containerd__containerd-4847 benchmark task container
"""
import asyncio
import json
from src.messenger import send_message


async def start_containerd_task():
    """Start containerd benchmark task container"""
    
    print("=" * 60)
    print("  Starting containerd__containerd-4847 Task Container")
    print("=" * 60 + "\n")
    
    # Request format - build from local Dockerfile
    request = {
        "participants": {},
        "config": {
            "task_config": {
                "task_name": "containerd__containerd-4847",
                "image": "containerd__containerd-4847:local",
                "command": "sleep infinity",
                "ports": {"22/tcp": None},
                "environment": {
                    "TERM": "xterm-256color"
                },
                "build_context": "containerd__containerd-4847",
                "dockerfile": "Dockerfile"
            }
        }
    }
    
    print("Sending request to Green Agent...")
    print(f"Task name: {request['config']['task_config']['task_name']}")
    print(f"Image tag: {request['config']['task_config']['image']}")
    print(f"Build path: {request['config']['task_config']['build_context']}\n")
    
    try:
        result = await send_message(
            message=json.dumps(request),
            base_url="http://localhost:9009",
            timeout=120
        )
        
        print("Container started successfully!\n")
        print("=" * 60)
        print(result["response"])
        print("=" * 60 + "\n")
        
        # Auto-copy trigger test script to container
        import subprocess
        import os
        
        print("Copying trigger test script...")
        script_path = os.path.join(os.path.dirname(__file__), "trigger_test_simple.py")
        copy_cmd = f"docker cp {script_path} containerd__containerd-4847:/trigger_test.py"
        subprocess.run(copy_cmd, shell=True, check=True, capture_output=True)
        print("trigger_test.py copied to container\n")
        
        print("Next steps:")
        print("1. SSH into container (use ssh_command above)")
        print("2. (Optional) Apply solution: bash /solution.sh")
        print("3. Trigger test: python3 /trigger_test.py")
        print("4. Check logs: ls -lh /scr/yuan/devops-greeen-agent/test_log_*")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(start_containerd_task())
