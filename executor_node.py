from sandbox import docker_sandbox
from schema import AlterEgo

sandbox = docker_sandbox()

def software_executor(state: AlterEgo):
    
    tool_output = state.get("tool_output", [])
    
    if not tool_output:
        return{
            "tool_output": [{
                "status": "error",
                "error": "No tool output found in the state."
            }]
        }
        
    software_output = []
    for item in tool_output:
        task_id = item.get("task_id")
        status = item.get("status")
        code = item.get("code")
        
        if status != "success" or not code:
            software_output.append({
                "task_id": task_id,
                "status": "error",
                "error": f"Task {task_id} failed or returned no code."
            })
            continue
        
        run_result = sandbox.code_runner_sandbox(code)
        
        software_output.append({
            "task_id": task_id,
            "status": "executed",
            "exit_code": run_result["exit_code"],
            "success": run_result["success"],
            "stdout": run_result["stdout"],
            "stderr": run_result["stderr"],
            "code": code
        })
        
    return {
        "tool_output": software_output
    }