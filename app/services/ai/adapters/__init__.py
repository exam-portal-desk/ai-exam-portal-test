"""
app/services/ai/adapters/__init__.py
Importing this package registers every built-in protocol adapter. To support a
new API protocol, add a module here that subclasses ProviderAdapter and is
decorated with @register_adapter, then import it below.
"""

from app.services.ai.adapters.base import (  # noqa: F401
    ProviderAdapter, get_adapter, list_protocols, register_adapter,
)
from app.services.ai.adapters import openai_compatible, gemini, anthropic  # noqa: F401
