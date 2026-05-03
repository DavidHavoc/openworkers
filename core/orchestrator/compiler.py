import os
from typing import Dict, Any, List
from core.schemas import BlackboardEntry

class PromptCompiler:
    def __init__(self, prompt_template_path: str = "promptForTheHead.md"):
        self.prompt_template_path = prompt_template_path
        self._template_cache = None

    def _load_template(self) -> str:
        if self._template_cache is None:
            if os.path.exists(self.prompt_template_path):
                with open(self.prompt_template_path, "r", encoding="utf-8") as f:
                    self._template_cache = f.read()
            else:
                self._template_cache = "You are the HEAD agent."
        return self._template_cache

    def compile_head_system_prompt(self, blackboard_entries: List[BlackboardEntry]) -> str:
        """
        Parses Blackboard state and dynamically injects task description, 
        worker evidence, and memory guidance into the system prompt.
        """
        template = self._load_template()
        
        task_descriptions = []
        worker_evidence = []
        memory_guidance = []

        # Parse blackboard state
        for entry in blackboard_entries:
            if entry.entry_type == "task":
                task_descriptions.append(entry.content.get("description", ""))
            elif entry.entry_type == "evidence":
                worker_evidence.append(entry.content.get("summary", ""))
            elif entry.entry_type == "memory_guidance":
                memory_guidance.append(entry.content.get("guidance", ""))
                
        # Construct the context section
        context_section = "\n\n--- CURRENT BLACKBOARD CONTEXT ---\n"
        if task_descriptions:
            context_section += "\n## TASK DESCRIPTION\n" + "\n".join(task_descriptions)
        if worker_evidence:
            context_section += "\n## WORKER EVIDENCE\n" + "\n".join(worker_evidence)
        if memory_guidance:
            context_section += "\n## MEMORY GUIDANCE\n" + "\n".join(memory_guidance)
            
        return template + context_section
