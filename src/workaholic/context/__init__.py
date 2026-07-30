"""Safe Workspace context and trusted embedded-profile configuration."""

from workaholic.context.errors import (
    ContextInvalidError,
    ContextNotFoundError,
    ContextStorageError,
    ProfileInvalidError,
    ProfileNotFoundError,
    ProfileUnsupportedError,
)
from workaholic.context.local import (
    CONTEXT_FILENAME,
    discover_workspace_context,
    exclude_context_from_git,
    read_current_workspace_context,
    write_current_workspace_context,
    write_workspace_context,
)
from workaholic.context.models import (
    DiscoveredWorkspace,
    EmbeddedProfile,
    LocalConfigPaths,
    LocalDataPaths,
    ProfileRegistry,
)
from workaholic.context.paths import (
    resolve_local_config_paths,
    resolve_local_data_paths,
)
from workaholic.context.profiles import load_profile_registry

__all__ = [
    "CONTEXT_FILENAME",
    "ContextInvalidError",
    "ContextNotFoundError",
    "ContextStorageError",
    "DiscoveredWorkspace",
    "EmbeddedProfile",
    "LocalConfigPaths",
    "LocalDataPaths",
    "ProfileInvalidError",
    "ProfileNotFoundError",
    "ProfileRegistry",
    "ProfileUnsupportedError",
    "discover_workspace_context",
    "exclude_context_from_git",
    "load_profile_registry",
    "read_current_workspace_context",
    "resolve_local_config_paths",
    "resolve_local_data_paths",
    "write_current_workspace_context",
    "write_workspace_context",
]
