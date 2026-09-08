from schema import AlterEgo

max_retries = 2

def code_checker(state: AlterEgo):
    tool_output = state.get("tool_output", [])
    current_retry_count = state.get("retry_count", 0) or 0
    
    error_tasks = []
    
    for item in tool_output:
        if item.get("status") == "executed" and not item.get("success", False):
            failed_code = item.get("code", "")
            stderr = item.get("stderr", "unknown execution error")
            
            prompt = (
                f"The following code failed execution during testing:\n\n"
                f"```python\n{failed_code}\n```\n\n"
                f"Docker Execution Error / Traceback:\n"
                f"```\n{stderr}\n```\n\n"
                f"Please analyze the error, fix the bug(s), and output the fully corrected, runnable Python script."
            )
            error_tasks.append(prompt)
        
    if not error_tasks:
        return {"tool_output": []}
    
    return {
        "tool_output": [{"tasks": error_tasks}],
        "retry_count": current_retry_count + 1
    }
    
def should_retry(state: AlterEgo):
    
    tool_output = state.get("tool_output", [])
    current_retry_count = state.get("retry_count", 0) or 0
    
    failures = any(
        item.get("status") == "executed" and not item.get("success", False)
        for item in tool_output
    )
    
    if failures and current_retry_count < max_retries:
        return "code_checker"
    
    return "complete"