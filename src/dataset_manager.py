"""Dataset manager - handles DevOps-Gym repository cloning and task resolution."""

import subprocess
import os
from pathlib import Path
from typing import Tuple


class DatasetManager:
    """Manages the DevOps-Gym dataset repository."""
    
    # Default settings
    REPO_URL = "https://github.com/ucsb-mlsec/DevOps-Gym.git"
    
    @staticmethod
    def _get_default_dataset_dir() -> Path:
        """
        Get the default dataset directory based on environment.
        
        Returns:
            Path to DevOps-Gym directory
        """
        # If running in a container with /DevOps-Gym mount, use that
        if os.path.exists('/DevOps-Gym'):
            return Path('/DevOps-Gym')
        
        # Otherwise use ./DevOps-Gym relative to current directory
        return Path.cwd() / "DevOps-Gym"
    
    def __init__(self, dataset_dir: Path = None, force_reclone: bool = False):
        """
        Initialize dataset manager.
        
        Args:
            dataset_dir: Path to DevOps-Gym directory. If None, auto-detects:
                         - /DevOps-Gym if running in container
                         - ./DevOps-Gym otherwise
            force_reclone: If True, delete existing dataset and re-clone from GitHub
        """
        self.dataset_dir = dataset_dir or self._get_default_dataset_dir()
        self.tasks_dir = self.dataset_dir / "tasks"
        self.force_reclone = force_reclone
    
    def ensure_dataset_available(self) -> Path:
        """
        Ensure DevOps-Gym dataset is available.
        
        If the dataset doesn't exist, clone it from GitHub.
        If it exists, just verify it's valid (unless force_reclone is True).
        
        Returns:
            Path to the dataset directory
        """
        # Handle force re-clone
        if self.force_reclone and self.dataset_dir.exists():
            print(f"Force re-clone requested. Removing existing dataset at: {self.dataset_dir}")
            import shutil
            try:
                shutil.rmtree(self.dataset_dir)
                print("Existing dataset removed successfully.")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to remove existing dataset at {self.dataset_dir}: {e}"
                ) from e
        
        if self.dataset_dir.exists():
            print(f"Dataset already exists at: {self.dataset_dir}")
            
            # Verify it's a valid git repo with tasks
            if not (self.dataset_dir / ".git").exists():
                raise RuntimeError(
                    f"Directory {self.dataset_dir} exists but is not a git repository. "
                    "Please remove it or specify a different location."
                )
            
            if not self.tasks_dir.exists():
                raise RuntimeError(
                    f"Directory {self.dataset_dir} exists but missing 'tasks' folder. "
                    "This may not be a valid DevOps-Gym repository."
                )
            
            print("Dataset verification passed.")
            return self.dataset_dir
        
        # Clone the repository
        print(f"Dataset not found. Cloning from {self.REPO_URL}...")
        print(f"Target directory: {self.dataset_dir}")
        
        try:
            subprocess.run(
                ["git", "clone", self.REPO_URL, str(self.dataset_dir)],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"Successfully cloned DevOps-Gym to {self.dataset_dir}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to clone DevOps-Gym repository:\n"
                f"Command: {' '.join(e.cmd)}\n"
                f"Error: {e.stderr}"
            ) from e
        
        return self.dataset_dir
    
    def resolve_task_path(self, task_identifier: str) -> Tuple[Path, str, str]:
        """
        Resolve task identifier to full path.
        
        Args:
            task_identifier: Either:
                - Full path: "issue_resolving/fasterxml__jackson-core-729"
                - Task name only: "fasterxml__jackson-core-729" (searches all types)
        
        Returns:
            Tuple of (task_path, task_type, task_name)
        
        Raises:
            FileNotFoundError: If task doesn't exist
            ValueError: If task_identifier is ambiguous (exists in multiple types)
        """
        # Ensure dataset is available
        self.ensure_dataset_available()
        
        # Case 1: Full path with task type (e.g., "issue_resolving/fasterxml__jackson-core-729")
        if "/" in task_identifier:
            task_type, task_name = task_identifier.split("/", 1)
            task_path = self.tasks_dir / task_type / task_name
            
            if not task_path.exists():
                raise FileNotFoundError(
                    f"Task not found: {task_identifier}\n"
                    f"Expected path: {task_path}"
                )
            
            return task_path, task_type, task_name
        
        # Case 2: Task name only - search all task types
        task_name = task_identifier
        task_types = ["build", "end_to_end", "issue_resolving", "monitor", "test_generation"]
        
        matches = []
        for task_type in task_types:
            task_path = self.tasks_dir / task_type / task_name
            if task_path.exists():
                matches.append((task_path, task_type, task_name))
        
        if len(matches) == 0:
            raise FileNotFoundError(
                f"Task '{task_name}' not found in any task type.\n"
                f"Searched: {', '.join(task_types)}"
            )
        
        if len(matches) > 1:
            found_in = [f"{t}/{n}" for _, t, n in matches]
            raise ValueError(
                f"Task '{task_name}' is ambiguous. Found in multiple types:\n" +
                "\n".join(f"  - {loc}" for loc in found_in) +
                "\n\nPlease specify the full path, e.g., 'issue_resolving/" + task_name + "'"
            )
        
        return matches[0]
    
    def get_task_info(self, task_identifier: str) -> dict:
        """
        Get task information including paths and metadata.
        
        Args:
            task_identifier: Task identifier (full path or name)
        
        Returns:
            Dictionary with task information:
            {
                'task_path': Path,
                'task_type': str,
                'task_name': str,
                'full_identifier': str,  # e.g., "issue_resolving/task-123"
                'dockerfile': Path,
                'task_yaml': Path,
                'tests_dir': Path or None,
                'run_tests_script': Path or None,
            }
        """
        task_path, task_type, task_name = self.resolve_task_path(task_identifier)
        
        return {
            'task_path': task_path,
            'task_type': task_type,
            'task_name': task_name,
            'full_identifier': f"{task_type}/{task_name}",
            'dockerfile': task_path / "Dockerfile",
            'task_yaml': task_path / "task.yaml",
            'tests_dir': task_path / "tests" if (task_path / "tests").exists() else None,
            'run_tests_script': task_path / "run-tests.sh" if (task_path / "run-tests.sh").exists() else None,
        }
    
    def list_tasks(self, task_type: str = None) -> list:
        """
        List available tasks.
        
        Args:
            task_type: If specified, only list tasks of this type.
                       If None, list all tasks from all types.
        
        Returns:
            List of task identifiers (e.g., ["issue_resolving/task-1", ...])
        """
        self.ensure_dataset_available()
        
        task_types = [task_type] if task_type else ["build", "end_to_end", "issue_resolving", "monitor", "test_generation"]
        
        tasks = []
        for ttype in task_types:
            type_dir = self.tasks_dir / ttype
            if not type_dir.exists():
                continue
            
            for task_dir in sorted(type_dir.iterdir()):
                if task_dir.is_dir() and (task_dir / "Dockerfile").exists():
                    tasks.append(f"{ttype}/{task_dir.name}")
        
        return tasks
