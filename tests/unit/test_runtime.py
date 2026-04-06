from __future__ import annotations

import pytest

from codeingme.runtime import (
    compact_write_plan,
    FilePatch,
    FilePatchHunk,
    FilePatchOperation,
    FilePatchPlan,
    PatchApplier,
    PatchConflictError,
    RollbackManager,
)


def test_patch_applier_and_rollback_restore_files(tmp_path) -> None:
    existing_file = tmp_path / "demo" / "existing.txt"
    existing_file.parent.mkdir(parents=True, exist_ok=True)
    existing_file.write_text("before", encoding="utf-8")

    applier = PatchApplier(tmp_path)
    rollback = RollbackManager()
    plan = FilePatchPlan(
        name="implementation",
        patches=[
            FilePatch(path="demo/existing.txt", content="after"),
            FilePatch(path="demo/new.txt", content="created"),
        ],
    )

    applied = applier.apply(plan)
    rollback.save("implementation", {"state": "implementation"}, applied_patches=applied)

    assert existing_file.read_text(encoding="utf-8") == "after"
    assert (tmp_path / "demo" / "new.txt").read_text(encoding="utf-8") == "created"

    restored = rollback.restore(tmp_path)

    assert restored is not None
    assert existing_file.read_text(encoding="utf-8") == "before"
    assert not (tmp_path / "demo" / "new.txt").exists()


def test_diff_patch_updates_file_and_can_be_rolled_back(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    applier = PatchApplier(tmp_path)
    rollback = RollbackManager()
    plan = FilePatchPlan(
        name="diff-update",
        patches=[
            FilePatch(
                path="demo/app.py",
                operation=FilePatchOperation.DIFF,
                hunks=[
                    FilePatchHunk(
                        start_line=2,
                        expected_lines=["line2"],
                        replacement_lines=["line2 updated", "line2b"],
                    )
                ],
            )
        ],
    )

    applied = applier.apply(plan)
    rollback.save("diff-update", {"state": "implementation"}, applied_patches=applied)

    assert target.read_text(encoding="utf-8") == "line1\nline2 updated\nline2b\nline3\n"

    rollback.restore(tmp_path)

    assert target.read_text(encoding="utf-8") == "line1\nline2\nline3\n"


def test_unified_diff_plan_parses_and_applies_incremental_changes(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    diff = """--- a/demo/app.py
+++ b/demo/app.py
@@ -1,3 +1,4 @@
 alpha
-beta
+beta updated
 gamma
+delta
"""
    plan = FilePatchPlan.from_unified_diff("incremental", diff)

    PatchApplier(tmp_path).apply(plan)

    assert target.read_text(encoding="utf-8") == "alpha\nbeta updated\ngamma\ndelta\n"


def test_compact_write_plan_converts_existing_file_updates_to_diff(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    plan = FilePatchPlan(
        name="write-update",
        patches=[FilePatch(path="demo/app.py", content="alpha\nbeta updated\ngamma\ndelta\n")],
    )

    compacted = compact_write_plan(tmp_path, plan)

    assert len(compacted.patches) == 1
    assert compacted.patches[0].operation is FilePatchOperation.DIFF

    PatchApplier(tmp_path).apply(compacted)

    assert target.read_text(encoding="utf-8") == "alpha\nbeta updated\ngamma\ndelta\n"


def test_compact_write_plan_skips_no_op_writes(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    plan = FilePatchPlan(
        name="write-update",
        patches=[FilePatch(path="demo/app.py", content="alpha\nbeta\n")],
    )

    compacted = compact_write_plan(tmp_path, plan)

    assert compacted.patches == []


def test_compact_write_plan_keeps_full_write_when_newline_mode_changes(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    plan = FilePatchPlan(
        name="write-update",
        patches=[FilePatch(path="demo/app.py", content="alpha\nbeta")],
    )

    compacted = compact_write_plan(tmp_path, plan)

    assert len(compacted.patches) == 1
    assert compacted.patches[0].operation is FilePatchOperation.WRITE


def test_compact_write_plan_keeps_write_for_missing_files(tmp_path) -> None:
    plan = FilePatchPlan(
        name="write-update",
        patches=[FilePatch(path="demo/new_app.py", content="print('created')\n")],
    )

    compacted = compact_write_plan(tmp_path, plan)

    assert len(compacted.patches) == 1
    assert compacted.patches[0].operation is FilePatchOperation.WRITE


def test_compact_write_plan_tracks_sequential_writes_to_same_file(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    plan = FilePatchPlan(
        name="write-update",
        patches=[
            FilePatch(path="demo/app.py", content="alpha\nbeta updated\n"),
            FilePatch(path="demo/app.py", content="alpha\nbeta final\n"),
        ],
    )

    compacted = compact_write_plan(tmp_path, plan)

    assert [patch.operation for patch in compacted.patches] == [
        FilePatchOperation.DIFF,
        FilePatchOperation.DIFF,
    ]

    PatchApplier(tmp_path).apply(compacted)

    assert target.read_text(encoding="utf-8") == "alpha\nbeta final\n"


def test_diff_patch_detects_conflicts(tmp_path) -> None:
    target = tmp_path / "demo" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("line1\nlineX\nline3\n", encoding="utf-8")
    plan = FilePatchPlan(
        name="conflict",
        patches=[
            FilePatch(
                path="demo/app.py",
                operation=FilePatchOperation.DIFF,
                hunks=[FilePatchHunk(start_line=2, expected_lines=["line2"], replacement_lines=["line2 updated"])],
            )
        ],
    )

    with pytest.raises(PatchConflictError, match="Patch conflict"):
        PatchApplier(tmp_path).apply(plan)
