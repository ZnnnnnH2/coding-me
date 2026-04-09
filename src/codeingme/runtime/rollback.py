"""提供工作区检查点与回滚能力。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .patches import AppliedFilePatch


@dataclass(slots=True)
class Checkpoint:
    name: str
    state_snapshot: dict[str, object]
    files: list["FileSnapshot"] = field(default_factory=list)


@dataclass(slots=True)
class FileSnapshot:
    path: str
    existed: bool
    previous_content: str | None


@dataclass(slots=True)
class RollbackManager:
    checkpoints: list[Checkpoint] = field(default_factory=list)

    def save(
        self,
        name: str,
        state_snapshot: dict[str, object],
        applied_patches: list[AppliedFilePatch] | None = None,
    ) -> Checkpoint:
        checkpoint = Checkpoint(
            name=name,
            state_snapshot=state_snapshot,
            files=[
                FileSnapshot(
                    path=patch.path,
                    existed=patch.existed,
                    previous_content=patch.previous_content,
                )
                for patch in applied_patches or []
            ],
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    def latest(self) -> Checkpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None

    def restore(self, root_dir: Path | str, checkpoint: Checkpoint | None = None) -> Checkpoint | None:
        target = checkpoint or self.latest()
        if target is None:
            return None
        root_path = Path(root_dir)
        for file_snapshot in reversed(target.files):
            path = root_path / file_snapshot.path
            if file_snapshot.existed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(file_snapshot.previous_content or "", encoding="utf-8")
                continue
            if path.exists():
                path.unlink()
        return target
