"""计算器工具"""

import ast
import operator
import math
from typing import Dict, Any

from domains.tool.infrastructure.base import Tool


class CalculatorTool(Tool):
    """Python计算器工具"""

    # 支持的操作符
    OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.BitXor: operator.xor,
        ast.USub: operator.neg,
    }

    # 支持的函数
    FUNCTIONS = {
        'abs': abs,
        'round': round,
        'max': max,
        'min': min,
        'sum': sum,
        'sqrt': math.sqrt,
        'sin': math.sin,
        'cos': math.cos,
        'tan': math.tan,
        'log': math.log,
        'exp': math.exp,
        'pi': math.pi,
        'e': math.e,
    }

    def __init__(self):
        super().__init__(
            name="python_calculator",
            description="执行数学计算。支持基本运算、数学函数等。例如：2+3*4, sqrt(16), sin(pi/2)等。"
        )

    def run(self, parameters: Dict[str, Any]) -> str:
        """
        执行计算

        Args:
            parameters: 包含input参数的字典

        Returns:
            计算结果
        """
        # 支持两种参数格式：input 和 expression
        expression = parameters.get("input", "") or parameters.get("expression", "")
        # strip 后判空：纯空白输入同样属于「没有表达式」，
        # 否则会把 Python 的 SyntaxError 文本（unexpected indent）抛给用户
        if not isinstance(expression, str) or not expression.strip():
            return "错误：计算表达式不能为空"
        expression = expression.strip()

        print(f"🧮 正在计算: {expression}")

        try:
            # 解析表达式
            node = ast.parse(expression, mode='eval')
            result = self._eval_node(node.body)
            result_str = str(result)
            print(f"✅ 计算结果: {result_str}")
            return result_str
        except SyntaxError:
            # 语法错误只说「表达式无法解析」，不把 Python 的
            # unexpected indent / invalid syntax 等底层文本透给用户
            return "错误：计算表达式语法无法解析，请检查表达式格式"
        except ZeroDivisionError:
            return "错误：除数不能为零"
        except (TypeError, ValueError) as exc:
            return f"错误：表达式无法计算（{type(exc).__name__}）"
        except Exception as e:
            # 兜底：仍不透传原始异常文本，避免泄漏内部实现细节
            error_msg = f"计算失败: {type(e).__name__}"
            print(f"❌ {error_msg}")
            return error_msg

    def _eval_node(self, node):
        """递归计算AST节点"""
        if isinstance(node, ast.Constant):  # Python 3.8+
            return node.value
        elif isinstance(node, ast.Num):  # Python < 3.8
            return node.n
        elif isinstance(node, ast.BinOp):
            return self.OPERATORS[type(node.op)](
                self._eval_node(node.left),
                self._eval_node(node.right)
            )
        elif isinstance(node, ast.UnaryOp):
            return self.OPERATORS[type(node.op)](self._eval_node(node.operand))
        elif isinstance(node, ast.Call):
            func_name = node.func.id
            if func_name in self.FUNCTIONS:
                args = [self._eval_node(arg) for arg in node.args]
                return self.FUNCTIONS[func_name](*args)
            else:
                raise ValueError(f"不支持的函数: {func_name}")
        elif isinstance(node, ast.Name):
            if node.id in self.FUNCTIONS:
                return self.FUNCTIONS[node.id]
            else:
                raise ValueError(f"未定义的变量: {node.id}")
        else:
            raise ValueError(f"不支持的表达式类型: {type(node)}")

    def get_parameters(self):
        """获取工具参数定义"""
        from ..base import ToolParameter
        return [
            ToolParameter(
                name="input",
                type="string",
                description="要计算的数学表达式，支持基本运算和数学函数",
                required=True
            )
        ]


# 便捷函数
def calculate(expression: str) -> str:
    """
    执行数学计算

    Args:
        expression: 数学表达式

    Returns:
        计算结果字符串
    """
    tool = CalculatorTool()
    return tool.run({"input": expression})
