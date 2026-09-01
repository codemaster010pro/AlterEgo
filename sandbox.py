import docker

class docker_sandbox:
    """sandbox for runing code from opencode to test its functionality,
    responsibility of this class is to run the code in a docker container
    and return the output of the code"""
    
    def __init__(self, image:str = "alterego-sandbox:latest"):
        self.client = docker.from_env()
        self.image = image
        
        try:
            self.client.images.get(self.image)
        except docker.errors.ImageNotFound:
            print(f"Warning: Image '{self.image}' not found locally. Build it using 'docker build -t {self.image} .'")
        
    def code_runner_sandbox(self, code:str, timeout:int = 10):
        """run the code in a docker container and return the output of the code"""
        container = None
        try:
            container = self.client.containers.run(
                self.image,
                command="tail -f /dev/null",
                detach=True,
                mem_limit="128m",
                nano_cpus=500000000,
                network_mode="none",
            )
            formatted_code = code.replace('"', '\\"').replace('\n', '\\n')
            formatted_cmd = f"python3 -c \"{formatted_code}\""
            
            execution = container.exec_run(
                cmd = formatted_cmd,
                demux=True
            )
            
            exit_code = execution.exit_code
            stdout_bytes, stderr_bytes = execution.output
            stdout = stdout_bytes.decode("utf-8").strip() if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8").strip() if stderr_bytes else ""
            
            return {
                "success": exit_code == 0,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr
            }
                
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e)
            } 
            
        finally:
            if container:
                try:
                    container.stop()
                    container.remove(force=True)
                except Exception:
                    pass
                
if __name__ == "__main__":
    sandbox = docker_sandbox()
    