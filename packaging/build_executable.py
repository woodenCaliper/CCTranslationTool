"""Utilities for packaging CCTranslationTool as a Windows executable.

The default build mode produces an ``onedir`` distribution instead of a single
``.exe`` file. Shipping the unpacked directory significantly lowers the chance
that Windows Defender flags the binary as ``Program:Script/Wacapew.A!ml``, a
heuristic that is commonly triggered by PyInstaller's one-file bootstrapper.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "package"
EXECUTABLE_NAME = "CCTranslationTool.exe"
_BASE_NAME = EXECUTABLE_NAME.replace(".exe", "")
ICON_SOURCE = REPO_ROOT / "icon" / "CCT_icon.png"
ICON_CONVERTED = REPO_ROOT / "icon" / "CCT_icon.ico"


def _check_platform() -> None:
    """Ensure the script is executed on Windows."""

    if sys.platform != "win32":
        raise SystemExit(
            "PyInstaller can only build a native Windows executable on Windows. "
            "Please run this script from a Windows environment."
        )


def _ensure_pyinstaller() -> None:
    """Validate that PyInstaller is available before attempting the build."""

    try:
        import PyInstaller  # noqa: F401  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency check
        raise SystemExit(
            "PyInstaller is required. Install it with `pip install pyinstaller`."
        ) from exc


def _prepare_icon() -> Path:
    """Convert the source PNG into an ICO file for PyInstaller."""

    if not ICON_SOURCE.exists():
        raise SystemExit(f"Icon asset not found at {ICON_SOURCE!s}.")

    try:
        from PIL import Image  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency check
        raise SystemExit(
            "Pillow is required to prepare the application icon. "
            "Install it with `pip install pillow`."
        ) from exc

    ICON_CONVERTED.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(ICON_SOURCE) as image:
        image = image.convert("RGBA")
        width, height = image.size
        max_dim = max(width, height)
        if width != height:
            square = Image.new("RGBA", (max_dim, max_dim), (0, 0, 0, 0))
            offset = ((max_dim - width) // 2, (max_dim - height) // 2)
            square.paste(image, offset)
            image = square

        available_sizes = [
            size for size in (256, 128, 64, 48, 32, 24, 16) if size <= image.size[0]
        ]
        if not available_sizes:
            available_sizes = [image.size[0]]

        icon_sizes = [(size, size) for size in available_sizes]
        image.save(ICON_CONVERTED, format="ICO", sizes=icon_sizes)

    return ICON_CONVERTED


def build(mode: str = "onedir") -> Path:
    """Create an executable using PyInstaller.

    Parameters
    ----------
    mode:
        ``"onedir"`` (default) keeps the unpacked distribution to mitigate
        Windows Defender false positives. ``"onefile"`` mirrors the previous
        single-file behaviour.

    Returns
    -------
    Path
        Location of the generated executable.
    """

    if mode not in {"onedir", "onefile"}:
        raise SystemExit("mode must be either 'onedir' or 'onefile'.")

    _check_platform()
    _ensure_pyinstaller()
    icon_path = _prepare_icon()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--noconsole",
        "--name",
        _BASE_NAME,
        "--icon",
        str(icon_path),
        "--add-data",
        f"{ICON_SOURCE};icon",
        str(REPO_ROOT / "translator_app.py"),
    ]

    if mode == "onefile":
        command.append("--onefile")
    else:
        command.append("--onedir")

    subprocess.run(command, check=True, cwd=REPO_ROOT)

    dist_dir = REPO_ROOT / "dist"
    if mode == "onefile":
        executable_path = dist_dir / EXECUTABLE_NAME
        if not executable_path.exists():
            raise SystemExit(
                "PyInstaller did not produce the expected executable at "
                f"{executable_path!s}."
            )

        OUTPUT_DIR.mkdir(exist_ok=True)
        final_path = OUTPUT_DIR / EXECUTABLE_NAME
        shutil.move(str(executable_path), final_path)
        result_path: Path = final_path
    else:
        dist_dir_onedir = dist_dir / _BASE_NAME
        executable_path = dist_dir_onedir / EXECUTABLE_NAME
        if not executable_path.exists():
            raise SystemExit(
                "PyInstaller did not produce the expected onedir executable at "
                f"{executable_path!s}."
            )

        OUTPUT_DIR.mkdir(exist_ok=True)
        final_dir = OUTPUT_DIR / _BASE_NAME
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(dist_dir_onedir, final_dir)
        result_path = final_dir / EXECUTABLE_NAME

    # Remove temporary build artefacts generated by PyInstaller.
    shutil.rmtree(REPO_ROOT / "build", ignore_errors=True)
    shutil.rmtree(dist_dir, ignore_errors=True)
    spec_file = REPO_ROOT / f"{_BASE_NAME}.spec"
    if spec_file.exists():
        spec_file.unlink()

    return result_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build CCTranslationTool executables")
    parser.add_argument(
        "--mode",
        choices=("onedir", "onefile"),
        default="onedir",
        help=(
            "Distribution type. 'onedir' keeps the unpacked directory to lower "
            "Windows Defender false positives. 'onefile' matches the legacy "
            "single-executable output."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    args = _parse_args()
    path = build(mode=args.mode)
    print(f"Executable created at {path}")
