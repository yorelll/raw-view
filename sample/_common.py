"""本目录所有示例共用的工具函数（用户不需要修改这个文件）。

提供三件事：
1. 定位一个可用的 raw-view 命令行（**打包好的 exe 优先，源码运行兜底**）
2. 找不到时给出**与当前处境相符**的提示，而不是抛异常
3. 子进程调用 + 退出码检查 + 失败时回显 CLI 的 stderr

为什么源码兜底很重要
--------------------
``dist/raw-view/raw-view-cli.exe`` 需要先跑 ``release.bat``（内部先跑全量测试，
再跑 PyInstaller，约 5-8 分钟）。如果那是唯一的路径，则"进目录 `python xxx.py`
就能看到结果"这个目标实际是"先等 5 分钟构建"。

而 ``python -m raw_view`` 在仓库里本来就能直接跑（它只是没打包而已）。所以本模块
按"exe → 源码"的顺序查找：想要 exe 的用 exe，只想快速看效果的直接用源码。

示例脚本都以 ``import _common`` 引入；脚本在 ``sample/view/`` 等子目录里，靠每个
脚本开头的 bootstrap 三行把 ``sample/`` 加入 ``sys.path``。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: 本文件所在目录（仓库的 sample/）
SAMPLE_DIR = Path(__file__).resolve().parent
#: 仓库根目录（sample/ 的上一级）。仅当 sample/ 确实位于 raw-view 仓库内时才有意义，
#: 用 :func:`is_repo_checkout` 判断——被单独拷走后这里指向的是一个无关目录。
REPO_ROOT = SAMPLE_DIR.parent

#: 打包产物可能出现的几个位置（相对仓库根），按优先级排列。
#: 第一个是 ``release.bat`` 的布局；后两个是直接跑 PyInstaller 时 onedir/onefile
#: 各自的落点，这样提示里给的手工命令产物也能被认出来。
CLI_EXE_CANDIDATES = (
    Path("dist") / "raw-view" / "raw-view-cli.exe",   # release.bat（zip 版布局）
    Path("dist") / "raw-view-cli" / "raw-view-cli.exe",  # PyInstaller onedir
    Path("dist") / "raw-view-cli.exe",                # PyInstaller onefile
)

#: 环境变量：指向一个自备的 CLI 可执行文件。
CLI_ENV_VAR = "RAW_VIEW_CLI"

#: 已知的"不是命令行程序"的可执行文件名。``raw-view-gui.exe`` 是纯 GUI
#: （PE subsystem 2），拿它当 CLI 用会弹窗/挂住，必须在找到时就拒绝。
_NON_CLI_EXE_NAMES = frozenset({"raw-view-gui.exe", "raw-view.exe"})


def _prepare_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8，避免非中文 locale 下打印中文时崩溃。

    示例文本全是中文。Windows 上 Python 的 stdout 默认用系统本地编码
    （中文系统是 cp936，英文系统是 cp1252）——在 cp1252 下打印中文会抛
    ``UnicodeEncodeError``，脚本直接崩掉；重定向到文件时也会写出乱码。

    这与 CLI 自己的 ``raw_view.__main__._make_utf8_stdio`` 是同一套防御措施。
    用 ``errors="replace"`` 而不是 ``strict``：宁可个别字符显示成 ``?``，
    也不能让示例因为控制台编码问题跑不起来。
    """
    if sys.flags.utf8_mode or os.environ.get("PYTHONUTF8") == "1":
        return
    if "utf" in os.environ.get("PYTHONIOENCODING", "").lower():
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # pragma: no cover - 特殊宿主
                pass


_prepare_stdio()


# ── 环境判断 ──────────────────────────────────────────────────────────


def is_repo_checkout() -> bool:
    """当前 ``sample/`` 是否位于一个 raw-view 源码仓库内。

    判据是 ``REPO_ROOT/raw_view/__main__.py`` 存在——这是源码运行
    （``python -m raw_view``）与 ``release.bat`` 都存在的前提。

    被单独拷到别处时（"我只想拿示例来玩玩"），这个函数返回 False，提示语
    于是不会叫用户去跑一个他那里根本不存在的 ``release.bat``。
    """
    return (REPO_ROOT / "raw_view" / "__main__.py").is_file()


def _looks_like_cli(path: Path) -> tuple[bool, str]:
    """判断 *path* 是否能当**命令行**程序用。返回 ``(是否可用, 原因)``。

    只检查是不是文件还不够：``raw-view-gui.exe`` 也是文件，但它是纯 GUI
    （PE subsystem 2），作为 CLI 调用会弹出一个窗口并挂住调用方。
    """
    if not path.is_file():
        return False, f"不是已存在的文件：{path}"
    if path.name.lower() in _NON_CLI_EXE_NAMES:
        return False, (
            f"{path.name} 不是命令行程序"
            f"（{'它是 GUI 子系统，调用会弹窗并挂住' if path.name.lower() == 'raw-view-gui.exe' else '它是启动器'}）；"
            f"请指向 raw-view-cli.exe"
        )
    return True, ""


# ── 定位 CLI ──────────────────────────────────────────────────────────


def find_cli() -> list[str] | None:
    """返回执行 CLI 的命令前缀（list），找不到返回 ``None``。

    返回值是**命令前缀列表**而不是单个路径，因为源码运行需要两个元素
    （``[python, "-m raw_view"]``）。调用方把它交给 :func:`run_cli` 展开即可。

    查找顺序：

    1. 环境变量 ``RAW_VIEW_CLI``（用户显式指定，最高优先级）
    2. ``<仓库>/dist/raw-view/raw-view-cli.exe`` 等三个打包产物位置
    3. **源码兜底**：仓库内直接 ``[sys.executable, "-m", "raw_view"]``

    第 3 步是让"克隆 → 装依赖 → 跑脚本"变成几秒钟的关键；``release.bat``
    因此降级为"想要 exe 时再跑"。
    """
    override = os.environ.get(CLI_ENV_VAR)
    if override:
        path = Path(override).expanduser()
        usable, _reason = _looks_like_cli(path)
        return [str(path)] if usable else None

    for relative in CLI_EXE_CANDIDATES:
        candidate = REPO_ROOT / relative
        usable, _reason = _looks_like_cli(candidate)
        if usable:
            return [str(candidate)]

    if is_repo_checkout():
        # 源码运行：与用户在仓库里手敲 `python -m raw_view ...` 完全等价。
        return [sys.executable, "-m", "raw_view"]

    return None


def cli_failure_reason() -> str:
    """解释**为什么** :func:`find_cli` 没找到东西，供提示语使用（L-1）。

    只描述实际被检查过的位置，而不是笼统地说"找不到"——用户需要知道
    是他设的环境变量有问题，还是他压根没构建过。
    """
    override = os.environ.get(CLI_ENV_VAR)
    if override:
        path = Path(override).expanduser()
        _usable, reason = _looks_like_cli(path)
        return f"{CLI_ENV_VAR} 指向的路径不可用 —— {reason}"

    checked = "、".join(str(REPO_ROOT / r) for r in CLI_EXE_CANDIDATES)
    if not is_repo_checkout():
        return (
            f"这些位置都没有可用的 raw-view-cli.exe：{checked}；"
            f"并且当前目录不是 raw-view 源码仓库，无法源码运行"
        )
    return (
        f"这些位置都没有可用的 raw-view-cli.exe：{checked}；"
        f"并且当前 Python 里 import 不到 raw_view 包，无法源码运行"
    )


def require_cli(explicit: str | os.PathLike[str] | None = None) -> list[str]:
    """返回可用的命令前缀；不可用时打印提示并退出（不抛 traceback）。

    *explicit* 是脚本配置区的 ``CLI`` 变量：``None`` 表示"自动查找"。它也接受
    一个路径字符串（同 ``RAW_VIEW_CLI`` 的语义），方便只想改一行的用户。
    """
    if explicit:
        path = Path(explicit).expanduser()
        usable, reason = _looks_like_cli(path)
        if usable:
            return [str(path)]
        _diagnose_and_exit(failures=[f"脚本配置区里的 CLI = {str(path)!r}：{reason}"])
        raise SystemExit(1)  # pragma: no cover - _diagnose_and_exit 已退出

    found = find_cli()
    if found is not None:
        return found
    _diagnose_and_exit(failures=[cli_failure_reason()])
    raise SystemExit(1)  # pragma: no cover


# ── 失败时的提示 ──────────────────────────────────────────────────────


def _diagnose_and_exit(failures: list[str] | None = None) -> None:
    """打印**与当前处境相符**的补救办法，然后由调用方退出。

    三种情况提示完全不同，混为一谈会让用户照着无效的指引白折腾：

    * 设了 ``RAW_VIEW_CLI`` 但路径不对 → 回显他设的那个路径（L-1）；
    * 这里是 raw-view 仓库，但既没打包也没法源码运行 → 说明怎么跑；
    * 这里不是仓库（sample/ 被单独拷走）→ 让他去下载发布包（M-1）。
    """
    override = os.environ.get(CLI_ENV_VAR)
    repo = is_repo_checkout()

    print("=" * 70, file=sys.stderr)
    print("错误：找不到可用的 raw-view 命令行", file=sys.stderr)
    for detail in failures or []:
        print(f"  {detail}", file=sys.stderr)

    if override:
        # L-1：用户刚设了环境变量，却被告知去看另一个路径——必须回显这个路径。
        print("", file=sys.stderr)
        print(f"你设置了环境变量 {CLI_ENV_VAR}：", file=sys.stderr)
        print(f"    {override}", file=sys.stderr)
        print("但它不可用（见上）。请改成 raw-view-cli.exe 的完整路径，例如：", file=sys.stderr)
        print(f'    set {CLI_ENV_VAR}=D:\\path\\to\\raw-view-cli.exe', file=sys.stderr)
        print("或者清除该变量改用自动查找：", file=sys.stderr)
        print(f"    set {CLI_ENV_VAR}=", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        return

    if repo:
        print("", file=sys.stderr)
        print("你正在 raw-view 源码仓库里。以下任选一种：", file=sys.stderr)
        print("", file=sys.stderr)
        print("  1) 直接源码运行（最快，无需构建；本脚本本应自动走这条路，", file=sys.stderr)
        print("     走到这里说明当前 Python 里 import 不到 raw_view 包）：", file=sys.stderr)
        print(f"        {sys.executable} -c \"import raw_view; print('ok')\"", file=sys.stderr)
        print("     若失败，先安装依赖：", file=sys.stderr)
        print("        pip install -r requirements.txt", file=sys.stderr)
        print("", file=sys.stderr)
        print("  2) 构建打包版（想要 exe 时；会先跑全量测试，约 5-8 分钟）：", file=sys.stderr)
        print("        release.bat", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        return

    # M-1：sample/ 在仓库外，这里没有 release.bat，也没有 raw_view 包。
    print("", file=sys.stderr)
    print("当前目录不是一个 raw-view 源码仓库（没找到 raw_view/ 包），", file=sys.stderr)
    print("所以无法源码运行，也没有 release.bat 可跑。请：", file=sys.stderr)
    print("", file=sys.stderr)
    print("  1) 下载 raw-view 发布包（zip 版），解压后把环境变量指向其中的", file=sys.stderr)
    print("     raw-view-cli.exe：", file=sys.stderr)
    print(f'        set {CLI_ENV_VAR}=D:\\path\\to\\raw-view\\raw-view-cli.exe', file=sys.stderr)
    print("", file=sys.stderr)
    print("  2) 或者把 sample/ 放回 raw-view 源码仓库里，即可直接源码运行。", file=sys.stderr)
    print("=" * 70, file=sys.stderr)


# ── 执行 ──────────────────────────────────────────────────────────────


def run_cli(cli: list[str] | str | os.PathLike[str], *args: object) -> subprocess.CompletedProcess:
    """执行 CLI；失败时回显 stderr 并返回结果（不静默吞掉）。

    *cli* 既可以是 :func:`require_cli` 返回的命令前缀列表，也可以是一个路径
    字符串（便于用户把 ``CLI`` 直接写成路径）。

    返回 ``CompletedProcess`` 而不是抛出，是为了让示例脚本自己决定"失败后是否
    继续"；绝大多数脚本接着调用 :func:`check_ok` 立刻停下。
    """
    prefix = list(cli) if isinstance(cli, (list, tuple)) else [str(cli)]
    command = [*prefix, *[str(a) for a in args]]

    # 源码运行（`[python, -m, raw_view]`）时，子进程的 cwd 是示例脚本所在目录，
    # 那里 import 不到 raw_view 包。把仓库根放进 PYTHONPATH，效果等同于用户在
    # 仓库根目录手敲 `python -m raw_view ...`。打包 exe 不需要这一步。
    env = None
    if len(prefix) > 1:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(REPO_ROOT), env.get("PYTHONPATH", "")])
        )

    print("$ " + " ".join(command))
    result = subprocess.run(command, capture_output=True, env=env)
    if result.stdout:
        print(result.stdout.decode("utf-8", "replace").rstrip())
    if result.returncode != 0:
        # CLI 的失败原因在 stderr；吞掉它用户只会看到"没生成文件"。
        print("-" * 70, file=sys.stderr)
        print(f"命令失败（退出码 {result.returncode}）：", file=sys.stderr)
        print(result.stderr.decode("utf-8", "replace").rstrip(), file=sys.stderr)
        print("-" * 70, file=sys.stderr)
    return result


def check_ok(result: subprocess.CompletedProcess, what: str) -> None:
    """断言上一步成功；失败时以非零码退出，避免"静默产出空结果"。"""
    if result.returncode != 0:
        print(f"失败：{what}", file=sys.stderr)
        raise SystemExit(result.returncode)


def banner(title: str) -> None:
    """打印一段醒目的步骤标题，让示例输出易于阅读。"""
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    """创建目录（含父目录）并返回 Path。"""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def report_output(path: str | os.PathLike[str]) -> None:
    """报告一个输出文件的大小，确认真的写出来了。"""
    output = Path(path)
    if output.is_file():
        print(f"  -> 已生成 {output}（{output.stat().st_size} 字节）")
    else:
        print(f"  -> 警告：{output} 未生成", file=sys.stderr)


def expected_view_hint(kind: str) -> str:
    """针对不同输入格式，告诉用户"打开的图应该长什么样"（M-3）。

    新用户看到一张图，需要知道**什么样算对、什么样算错**，否则他不知道
    参数是不是写错了。这里按格式给出可判别的一句话。
    """
    hints = {
        "raw_8bit": (
            "打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变。\n"
            "     若整幅纯黑/纯白，多半是宽度或总字节数不对。"
        ),
        "raw_msb": (
            "打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变。\n"
            "     若明显偏亮发白或有台阶，通常是对齐方式（alignment）写错了：\n"
            "     把 msb 改成 lsb 再试。若出现斜纹/错行，是宽度不对。"
        ),
        "raw_lsb": (
            "打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变。\n"
            "     若明显偏亮发白或有台阶，通常是对齐方式写错了：把 lsb 改成 msb 再试。"
        ),
        "raw_packed": (
            "打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变。\n"
            "     若出现斜纹/错行，是宽度与 packed 约束不符；若整幅发灰，是格式名写错"
            "（例如把 RAW10 Packed 当成了未打包的 RAW10）。"
        ),
        "raw_bayer": (
            "打开 PNG 应看到红 / 绿 / 蓝 / 白竖条（彩色）。\n"
            "     若红蓝互换，是 bayer 排列写错（RGGB <-> BGGR）；若整体偏绿，是 GRBG/GBRG 弄混了。"
        ),
        "yonly": (
            "打开 PNG 应看到「左暗右亮、上暗下亮」的平滑渐变（灰度）。\n"
            "     若偏亮或出现台阶，是位深对齐/字节序写错：试试 --alignment lsb 或 --endianness big。"
        ),
        "yuv_422": (
            "打开 PNG 应看到亮度渐变，且**左右两半颜色不同**。\n"
            "     若颜色整片相同，说明色度平面被按另一种布局解释了——4:2:2 家族里\n"
            "     YUYV / YVYU / UYVY / VYUY 的字节顺序不同，换 --yuv-type 再试。"
        ),
        "yuv_420": (
            "打开 PNG 应看到亮度渐变，且**左右两半颜色不同**。\n"
            "     若颜色错乱或整片同色，说明平面顺序不对：4:2:0 家族里 I420 / YV12 是\n"
            "     U、V 平面顺序不同，NV12 / NV21 是 UV、VU 交错顺序不同。"
        ),
        "offset": (
            "下面的对照组（不加 --offset）应是错位的花屏，加了 --offset 的应是平滑渐变。\n"
            "     两者一样 = 文件没有头部（OFFSET 设为 0）；正确的那张也花屏 = OFFSET 值不对。"
        ),
    }
    return hints.get(kind, "")


# ── 图像/二进制的通用构造工具 ──────────────────────────────────────────
#
# 示例脚本自己造输入，用户不需要准备任何文件。这些函数只依赖 numpy；涉及
# 图像编码时按需 import cv2（示例脚本里已有依赖）。


def gradient_gray(width: int, height: int, dtype=None):
    """造一张横向 + 纵向都有变化的灰度图，便于肉眼确认"图是对的"。

    用渐变而不是纯色：纯色解码出来是均匀一片，无法判断 stride/对齐是否正确。
    """
    import numpy as np

    x = np.linspace(0, 1, width, dtype=np.float64)[None, :]
    y = np.linspace(0, 1, height, dtype=np.float64)[:, None]
    ramp = (x * 0.7 + y * 0.3) * 255.0
    return ramp.astype(np.uint8)


def color_test_pattern(width: int, height: int):
    """造一张 BGR 彩条 + 渐变图，用作 image -> RAW/YUV 的输入。

    包含几块纯色（红/绿/蓝/白/黑）和一段渐变：纯色块让 YUV 色度抽样是否正确
    一眼可见，渐变让位深/对齐错误（台阶、偏色）一眼可见。
    """
    import numpy as np

    image = np.zeros((height, width, 3), dtype=np.uint8)
    bars = [
        (255, 255, 255),   # 白
        (0, 255, 255),     # 黄 (BGR)
        (0, 255, 0),       # 绿
        (255, 255, 0),     # 青
        (0, 0, 255),       # 红
        (255, 0, 0),       # 蓝
        (0, 0, 0),         # 黑
    ]
    bar_width = max(1, width // len(bars))
    for index, colour in enumerate(bars):
        left = index * bar_width
        right = width if index == len(bars) - 1 else left + bar_width
        image[:, left:right] = colour
    # 叠加一层从左到右的亮度渐变，便于发现 stride / 对齐问题。
    ramp = np.linspace(0.35, 1.0, width, dtype=np.float32)[None, :, None]
    return np.clip(image.astype(np.float32) * ramp, 0, 255).astype(np.uint8)


def require_numpy():
    """示例脚本需要 numpy；缺失时给出安装提示而不是 ImportError traceback。"""
    try:
        import numpy as np

        return np
    except ImportError:  # pragma: no cover - 仅在缺依赖时触发
        print(
            "错误：本示例需要 numpy。请先安装依赖：\n"
            "    pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(1)
