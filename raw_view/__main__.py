import sys
from pathlib import Path

# 确保包目录在 Python 路径中
if __name__ == "__main__":
    # 获取当前文件所在目录的父目录（包根目录）
    pkg_root = Path(__file__).parent.parent
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))

    from raw_view.gui import run

    run()
