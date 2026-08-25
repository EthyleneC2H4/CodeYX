
from __future__ import annotations

import os
import tempfile
from pathlib import Path


class PathSandbox:


    def __init__(
        self,
        project_root: str,
        extra_allowed: list[str] | None = None,
    ) -> None:
        root = Path(project_root).resolve()
        self._allowed_roots: list[Path] = [root, Path(tempfile.gettempdir()).resolve()]
        if extra_allowed:
            for p in extra_allowed:
                self._allowed_roots.append(Path(p).resolve())


    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]

    @property
    def allowed_roots(self) -> list[Path]:
        """All allowed roots; index 0 is the project root."""
        return list(self._allowed_roots)


    def rebase(self, project_root: str) -> None:
        """Re-point the primary allowed root. Used by EnterWorktree /
        ExitWorktree so relative-path resolution follows the session's
        actual working root instead of the launch directory."""
        self._allowed_roots[0] = Path(project_root).resolve()


    def check(self, path: str) -> tuple[bool, str]:
        has_traversal = ".." in Path(path).parts

        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.project_root / p
        try:
            # 先通过 normpath 消除 ".." 组件，防止路径遍历
            abs_path = Path(os.path.normpath(str(p.absolute())))
        except ValueError:
            # Embedded NUL and friends: no real path can match, deny instead
            # of letting ValueError crash the turn.
            return False, f"非法路径: {path!r}"

        if has_traversal:
            # Traversal segments may only resolve strictly inside the project
            # root — not merely inside another allowed root such as the shared
            # tempdir. Anything else goes through user confirmation instead.
            try:
                # Non-strict: resolves symlinks of existing ancestors (e.g.
                # macOS /var -> /private/var) and leaves the missing tail.
                abs_path.resolve(strict=False).relative_to(self.project_root)
            except ValueError:
                return False, f"路径遍历超出项目根: {path}"

        try:
            real_path = abs_path.resolve(strict=True)
        except ValueError:
            return False, f"非法路径: {path!r}"
        except OSError:
            # Path (or part of its parent chain) doesn't exist yet — a write
            # to a new directory, e.g. the plan file. Resolve the deepest
            # existing ancestor and re-join the remaining components; nothing
            # in the missing segment can hide a symlink.
            real_path = None
            for anc in abs_path.parents:
                try:
                    real_anc = anc.resolve(strict=True)
                except OSError:
                    continue
                rel = abs_path.relative_to(anc)
                real_path = real_anc.joinpath(*rel.parts)
                break
            if real_path is None:
                return False, f"无法解析路径: {path}"

        for root in self._allowed_roots:
            try:
                real_path.relative_to(root)
                return True, ""
            except ValueError:
                continue

        return False, f"路径 {path} 超出沙箱范围"
