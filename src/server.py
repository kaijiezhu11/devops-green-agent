import argparse
import os
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

from executor import Executor


def main():
    parser = argparse.ArgumentParser(description="Run the A2A agent.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9009, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()
    
    # Allow AGENT_CARD_URL environment variable to override card-url
    card_url_override = args.card_url or os.getenv("AGENT_CARD_URL")

    # Agent card configuration
    # See: https://a2a-protocol.org/latest/tutorials/python/3-agent-skills-and-card/
    
    skill = AgentSkill(
        id="container-management",
        name="Docker Container Management",
        description="Start and manage Docker containers on the host machine, providing SSH access to task containers",
        tags=["docker", "container", "devops", "ssh", "infrastructure"],
        examples=[
            "Start a container named containerd__task-4847",
            "Launch an Ubuntu container with SSH access",
            "Create a task container with custom environment variables"
        ]
    )

    # Determine the URL to advertise
    # If host is 0.0.0.0 (bind all interfaces), use localhost for the card URL
    card_host = "localhost" if args.host == "0.0.0.0" else args.host
    
    agent_card = AgentCard(
        name="DevOps Green Agent",
        description="A green agent that can start and manage Docker containers on the host machine, providing SSH access for task execution and evaluation",
        url=card_url_override or f"http://{card_host}:{args.port}/",
        version='1.0.0',
        default_input_modes=['text'],
        default_output_modes=['text'],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill]
    )

    request_handler = DefaultRequestHandler(
        agent_executor=Executor(),
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    
    # Add custom endpoint for simple test triggering
    app = server.build()
    
    from starlette.responses import JSONResponse
    from starlette.requests import Request
    from starlette.routing import Route
    
    async def trigger_test(request: Request):
        """Simple endpoint to trigger tests without A2A protocol"""
        try:
            data = await request.json()
            
            # Call agent internal method directly
            from agent import Agent, RunTestConfig
            from docker_manager import DockerManager
            from datetime import datetime
            import os
            
            # Validate config
            config = RunTestConfig(**data)
            
            # Create agent and docker manager
            agent = Agent()
            docker_manager = agent.docker_manager
            
            if not docker_manager:
                return JSONResponse({
                    "success": False,
                    "error": "Docker is not available"
                }, status_code=500)
            
            # Execute tests
            result_text = []
            result_text.append(f"Running tests: {config.container_name}\n")
            
            # 1. Copy tests directory
            if config.copy_tests:
                tests_path = f"/workspace/{config.task_dir}/tests"
                docker_manager.copy_to_container(
                    config.container_name,
                    tests_path,
                    "/tests"
                )
                result_text.append("Copied tests directory to /tests")
            
            # 2. Copy run-tests.sh
            if config.copy_script:
                script_path = f"/workspace/{config.task_dir}/run-tests.sh"
                docker_manager.copy_to_container(
                    config.container_name,
                    script_path,
                    "/run-tests.sh"
                )
                result_text.append("Copied run-tests.sh to /run-tests.sh")
            
            # 3. Execute test script
            result_text.append(f"\nExecuting: bash {config.test_script}\n")
            result_text.append("=" * 60)
            
            output, exit_code = docker_manager.exec_command_in_container(
                config.container_name,
                f"bash {config.test_script}"
            )
            
            result_text.append(output)
            result_text.append("=" * 60)
            result_text.append(f"\nExit code: {exit_code}")
            
            # 4. Save log to host
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"test_log_{config.container_name}_{timestamp}.txt"
            log_path = f"/workspace/{log_filename}"
            
            with open(log_path, 'w') as f:
                f.write("\n".join(result_text))
            
            result_text.append(f"\nLog saved: {log_filename}")
            
            # Return result
            response_data = {
                "status": "passed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                "log_file": log_filename,
                "log_path": log_path
            }
            
            return JSONResponse({
                "success": True,
                "message": "\n".join(result_text),
                "data": response_data
            })
                
        except Exception as e:
            import traceback
            return JSONResponse({
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }, status_code=500)
    
    # Add the route
    app.routes.append(Route("/trigger-test", trigger_test, methods=["POST"]))
    
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
