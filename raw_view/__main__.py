"""Entry point: python -m raw_view"""

import sys
from pathlib import Path

if __name__ == "__main__":
    pkg_root = Path(__file__).parent.parent
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))

    from raw_view.gui.app import run

    run()
