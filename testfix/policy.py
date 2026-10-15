"""Conservative Python subset for the local development runner.

This is an additional language restriction, not an OS security sandbox. Docker
is still recommended for untrusted workloads. Public API/signature is locked.
"""
from __future__ import annotations

import ast

SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset", "int",
    "isinstance", "len", "list", "map", "max", "min", "next", "ord", "chr", "pow", "range",
    "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
    "Exception", "ValueError", "TypeError", "IndexError", "ZeroDivisionError", "NotImplementedError",
}
SAFE_ATTRIBUTES = {
    "append", "extend", "insert", "pop", "remove", "clear", "copy", "count", "index", "sort", "reverse",
    "get", "keys", "values", "items", "setdefault", "update", "add", "discard", "union", "intersection",
    "difference", "issubset", "issuperset", "split", "rsplit", "splitlines", "join", "strip", "lstrip",
    "rstrip", "replace", "startswith", "endswith", "lower", "upper", "isdigit", "isalpha", "isalnum",
    "find", "rfind", "capitalize", "swapcase", "title", "is_integer", "bit_length",
    "sqrt", "floor", "ceil", "fabs", "log", "log2", "log10", "exp", "sin", "cos", "tan", "gcd",
    "factorial", "isclose", "isfinite", "pi", "e", "inf", "prod", "comb", "perm", "lcm",
}
SAFE_IMPORTS = {
    "typing": {"List", "Tuple", "Dict", "Set", "Optional", "Union", "Iterable", "Sequence", "Any"},
    "math": SAFE_ATTRIBUTES & {"sqrt", "floor", "ceil", "fabs", "log", "log2", "log10", "exp", "sin",
                               "cos", "tan", "gcd", "factorial", "isclose", "isfinite", "pi", "e", "inf",
                               "prod", "comb", "perm", "lcm"},
}
FORBIDDEN_NAMES = {"open", "eval", "exec", "compile", "input", "print", "globals", "locals", "vars",
                   "getattr", "setattr", "delattr", "type", "object", "super", "help", "dir", "breakpoint",
                   "exit", "quit", "memoryview"}


class PolicyError(ValueError):
    pass


def validate_acceptance_code(code: str) -> None:
    """Reject missing/vacuous judge inputs before claiming independent evidence.

    These are trusted evaluator-owned scripts, not subjected to candidate AST
    restrictions. Static checks validate structure, not test completeness.
    """
    if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 200_000:
        raise ValueError("Independent acceptance must be nonempty Python, at most 200 KB")
    try:
        tree = ast.parse(code)
    except SyntaxError:
        raise ValueError("Independent acceptance contains invalid Python") from None
    checks = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "check"]
    if len(checks) != 1 or len(checks[0].args.posonlyargs) + len(checks[0].args.args) != 1:
        raise ValueError("Independent acceptance must define exactly one check(candidate) function")
    if not any(isinstance(node, (ast.Assert, ast.Raise)) for node in ast.walk(tree)):
        raise ValueError("Independent acceptance must contain an assertion or explicit failure; empty/pass judges are rejected")


def signature(node: ast.FunctionDef) -> str:
    # Annotation spelling and docstrings may change; argument shape/defaults may not.
    args = node.args
    return repr(([(x.arg) for x in args.posonlyargs], [x.arg for x in args.args],
                 args.vararg.arg if args.vararg else None, [x.arg for x in args.kwonlyargs],
                 args.kwarg.arg if args.kwarg else None,
                 [ast.dump(x) for x in args.defaults], [ast.dump(x) if x else None for x in args.kw_defaults]))


def validate_candidate(code: str, initial_code: str, entry_point: str) -> None:
    if len(code.encode("utf-8")) > 40_000:
        raise PolicyError("Patch exceeds 40 KB")
    try:
        tree = ast.parse(code)
        initial = ast.parse(initial_code)
    except SyntaxError as exc:
        raise PolicyError(f"Python syntax error: {exc.msg}") from None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                raise PolicyError("Module level permits only imports, functions and docstrings")
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    originals = {node.name: node for node in initial.body if isinstance(node, ast.FunctionDef)}
    declared_names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(declared_names) != len(set(declared_names)):
        raise PolicyError("Duplicate module function definitions are forbidden")
    def import_bindings(module):
        bindings = {}
        for node in module.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound = alias.asname or alias.name.split('.')[0]
                    identity = (type(node).__name__, getattr(node, "module", None), getattr(node, "level", 0), alias.name)
                    if bound in bindings and bindings[bound] != identity:
                        raise PolicyError("Module import bindings may not be rebound")
                    bindings[bound] = identity
        return bindings
    original_bindings, final_bindings = import_bindings(initial), import_bindings(tree)
    original_names = set(originals) | set(original_bindings)
    if (set(functions) | set(final_bindings)) & (SAFE_BUILTINS - original_names):
        raise PolicyError("New module bindings may not shadow safe builtins used by existing functions")
    for name, identity in original_bindings.items():
        if final_bindings.get(name) != identity:
            raise PolicyError(f"Existing import binding {name} must be preserved")
    if entry_point not in functions or signature(functions[entry_point]) != signature(originals[entry_point]):
        raise PolicyError("Locked entry point/signature changed")
    for name, original in originals.items():
        if name != entry_point and (name not in functions or ast.dump(functions[name]) != ast.dump(original)):
            raise PolicyError(f"Existing helper {name} may not change")
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal, ast.With,
                             ast.AsyncWith, ast.Await, ast.Yield, ast.YieldFrom)):
            raise PolicyError(f"Unsupported statement: {type(node).__name__}")
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.name.startswith("_")):
            raise PolicyError("Decorators and private function names are forbidden")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in FORBIDDEN_NAMES):
            raise PolicyError(f"Forbidden name: {node.id}")
        if isinstance(node, ast.arg) and node.arg.startswith("_"):
            raise PolicyError("Private argument names are forbidden")
        if isinstance(node, ast.Attribute) and node.attr not in SAFE_ATTRIBUTES:
            raise PolicyError(f"Unsupported attribute: {node.attr}")
        if isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
            raise PolicyError("Attribute assignment/deletion is forbidden")
        if isinstance(node, ast.Import):
            if any(alias.name != "math" for alias in node.names):
                raise PolicyError("Only 'import math' is allowed")
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in SAFE_IMPORTS or any(alias.name not in SAFE_IMPORTS[node.module] for alias in node.names):
                raise PolicyError("Unsupported import")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(alias.asname and alias.asname.startswith("_") for alias in node.names):
                raise PolicyError("Private import aliases are forbidden")
            if any((alias.asname or alias.name.split('.')[0]) in functions for alias in node.names):
                raise PolicyError("Imports may not shadow module functions")

