"""强制编辑器跑在项目虚拟环境 ``.venv`` 里。

为什么要强制
------------
音频后端（``pygame-ce``）与 numpy 只装在项目 ``.venv`` 中。用别的解释器启动时，
编辑器会退化成静音时钟，甚至因为缺少依赖而报错 —— 让“用哪个 Python”变成隐式条件
非常容易踩坑。这里的策略是**治本**：

* 只要 ``.venv`` 存在，就用它的解释器**重新启动自己**（无论当前解释器是否装了 pygame）；
* ``.venv`` 不存在时给出创建命令并退出（不会去改动系统 Python）；
* 想在别的解释器里调试时，设环境变量 ``VERUS_ALLOW_ANY_PYTHON=1`` 跳过这一步。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional, Sequence

ENV_ALLOW_ANY = "VERUS_ALLOW_ANY_PYTHON"
ENV_REEXEC = "VERUS_VENV_REEXEC"


def project_root() -> str:
    """仓库根目录（``src/rhythm_editor/venv_guard.py`` 往上三层）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def venv_python(root: Optional[str] = None) -> str:
    """项目 ``.venv`` 里的解释器路径（不存在时返回空串）。"""
    root = root or project_root()
    if sys.platform == "win32":
        candidate = os.path.join(root, ".venv", "Scripts", "python.exe")
    else:
        candidate = os.path.join(root, ".venv", "bin", "python")
    return candidate if os.path.isfile(candidate) else ""


def running_in_project_venv(root: Optional[str] = None) -> bool:
    """当前解释器是否就是项目 ``.venv`` 里的那个。"""
    interpreter = venv_python(root)
    if not interpreter:
        return False
    try:
        return os.path.samefile(interpreter, sys.executable)
    except OSError:  # pragma: no cover - 极端情况
        return os.path.normcase(os.path.abspath(interpreter)) == os.path.normcase(
            os.path.abspath(sys.executable)
        )


def venv_install_hint(root: Optional[str] = None) -> str:
    """创建虚拟环境并安装依赖的命令（给用户照抄）。"""
    root = root or project_root()
    if sys.platform == "win32":
        activate = r".\.venv\Scripts\Activate.ps1"
        python = r".\.venv\Scripts\python.exe"
    else:
        activate = "source .venv/bin/activate"
        python = ".venv/bin/python"
    return (
        f"  cd /d \"{root}\"\n"
        f"  python -m venv .venv\n"
        f"  {python} -m pip install -r requirements.txt\n"
        f"  （激活：{activate}）"
    )


def ensure_project_venv(entry: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> None:
    """保证运行在项目 ``.venv`` 里；必要时用 ``.venv`` 重启当前进程。

    :param entry: 重新启动时执行的脚本（默认 ``run_editor.py``）
    :param argv: 传给脚本的参数（默认 ``sys.argv[1:]``）

    正常（已经在 ``.venv`` 里 / 允许任意解释器）时直接返回；
    需要切换时本函数**不会返回**（抛 ``SystemExit``）。
    """
    if os.environ.get(ENV_ALLOW_ANY):
        return
    if running_in_project_venv():
        return
    root = project_root()
    interpreter = venv_python(root)
    args = list(sys.argv[1:] if argv is None else argv)
    if not interpreter:
        message = (
            "没有找到项目虚拟环境 .venv —— 编辑器只在虚拟环境里运行（音频依赖装在那里）。\n"
            f"当前解释器：{sys.executable}\n"
            "请先创建：\n" + venv_install_hint(root) + "\n"
            f"（临时跳过这个检查：设环境变量 {ENV_ALLOW_ANY}=1）"
        )
        print(message, file=sys.stderr)
        raise SystemExit(2)
    script = entry or os.path.join(root, "run_editor.py")
    print(
        f"[venv] 当前解释器 {sys.executable} 不是项目 .venv，改用 {interpreter} 重新启动",
        file=sys.stderr,
    )
    env = dict(os.environ, **{ENV_REEXEC: "1"})
    try:
        completed = subprocess.run([interpreter, script, *args], env=env)
    except OSError as exc:  # pragma: no cover - 虚拟环境损坏
        print(f"[venv] 启动虚拟环境失败（{exc}），请检查 .venv。", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(completed.returncode)
