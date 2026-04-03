"""
Mock out modules with Python 3.10+ syntax so test collection works on Python 3.9.
These mocks are applied BEFORE any tool modules are imported.
"""

import sys
from unittest.mock import MagicMock

# Modules that use `type | None` syntax and break Python 3.9 collection
_MOCK_MODULES = {
    "tools.environments",
    "tools.environments.base",
    "tools.environments.singularity",
    "tools.terminal_tool",
    "tools.vision_tools",
    "tools.mixture_of_agents_tool",
    "tools.image_generation_tool",
    "tools.web_tools",
    "tools.registry",
    "model_tools",
    "tools.model_tools",
}

for mod_name in _MOCK_MODULES:
    if mod_name not in sys.modules:
        mock_mod = MagicMock()
        sys.modules[mod_name] = mock_mod
