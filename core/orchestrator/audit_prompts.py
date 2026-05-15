"""Shared prompt loader for the code-audit family of orchestrators.

Lives outside any single flow module so each new auditor can register
its templates without circular imports. Deliberately not reusing
``PromptCompiler``: that compiler is wired to extract thesis blackboard
state, which audit flows don't use. Audit templates only need
``{{ var }}`` substitution.
"""

from __future__ import annotations

import os
from typing import Any

_AUDIT_PROMPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "prompts",
    "code_audit",
)

# Registry of template name → filename under ``prompts/code_audit/``.
# Each new auditor appends its entries here; the renderer accepts any
# registered name without further changes elsewhere.
TEMPLATE_FILES: dict[str, str] = {
    "readme_planner": "readme_planner.md",
    "readme_checker": "readme_checker.md",
    "pr_planner": "pr_planner.md",
    "pr_checker": "pr_checker.md",
    "audit_critic": "audit_critic.md",
}


def render_audit_prompt(name: str, variables: dict[str, Any]) -> str:
    filename = TEMPLATE_FILES.get(name)
    if not filename:
        raise ValueError(f"Unknown audit template: {name}")
    path = os.path.join(_AUDIT_PROMPT_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            template = f.read()
    except OSError:
        return f"[Template {name} not found at {path}]"
    for key, value in variables.items():
        template = template.replace("{{ " + key + " }}", str(value))
    return template
