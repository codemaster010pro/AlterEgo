import queue
import subprocess
from typing import Any
import os

def merge_dict(existing: dict, new_input: dict):
    updated = existing.copy()
    updated.update(new_input)
    return updated

def overwrite_append_list(existing: list[dict], new_input: list[dict]):
    if new_input == []:
        return []
    return existing + new_input

def run_opencode(task:str,task_id:int, result:queue.Queue[Any]):
    """Runs a single OpenCode task and puts the output into a thread-safe Queue."""
    file_name = f"opencode_{task_id}.py"
    
    prompt = f"{task}.save the code in a file named {file_name} and return the file name only."
    cmd = ["opencode", "run", prompt,"--yes","--non-interactive"]
    
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=90
        )
        
        if os.path.exists(file_name):
            with open(file_name, 'r') as f:
                code_content = f.read()
                
                os.remove(file_name)  # Clean up the temporary file after reading its content
                
                result.put({
                    "task_id": task_id,
                    "status": "success",
                    "code": code_content
                })
                
        else:
            result.put({
                "task_id": task_id,
                "status": "error",
                "code": process.stdout
            })
            
    except subprocess.CalledProcessError as e:
        result.put({
            "task_id": task_id,
            "status": "error",
            "error": f"CLI error: {e.stderr}"
        })
        
    except subprocess.TimeoutExpired as e:
        result.put({
            "task_id": task_id,
            "status": "error",
            "error": f"Opencode execution timed out after 90 seconds."
        })