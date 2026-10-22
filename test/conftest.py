"""让 pytest 把项目根目录加入 ``sys.path``。

本项目没有打包（没有 ``pyproject.toml`` / ``setup.cfg``），用例里直接 import
``domains.*``。pytest 在 ``rootdir`` 下发现 ``conftest.py`` 时会把它所在目录放进
``sys.path``（``importmode=prepend`` 的默认行为），缺了这个文件就会在运行 pytest
时报 ``ModuleNotFoundError: domains``——而在项目目录里手动跑脚本却是好的。
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
