"""导出运行时补丁、执行与回滚工具。"""

from .executor import (
    CommandResult,
    ContainerTestConfig,
    PreviewLaunchConfig,
    PreviewLaunchResult,
    RuntimeExecutor,
)
from .patches import (
    AppliedFilePatch,
    compact_write_plan,
    FilePatch,
    FilePatchHunk,
    FilePatchOperation,
    FilePatchPlan,
    PatchApplier,
    PatchConflictError,
    render_patch_unified_diff,
)
from .rollback import Checkpoint, RollbackManager

__all__ = [
    "AppliedFilePatch",
    "Checkpoint",
    "compact_write_plan",
    "CommandResult",
    "ContainerTestConfig",
    "FilePatch",
    "FilePatchHunk",
    "FilePatchOperation",
    "FilePatchPlan",
    "PatchApplier",
    "PatchConflictError",
    "PreviewLaunchConfig",
    "PreviewLaunchResult",
    "render_patch_unified_diff",
    "RollbackManager",
    "RuntimeExecutor",
]
