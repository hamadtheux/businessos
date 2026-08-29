from __future__ import annotations

import ast
from pathlib import Path


VERSIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"


def _string_arguments(node: ast.Call) -> set[str]:
    return {
        argument.value
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }


def _string_list(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return set()
    return {
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def _column_helpers(tree: ast.Module) -> dict[str, set[str]]:
    helpers: dict[str, set[str]] = {}
    for function in (
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        columns = {
            str(call.args[0].value)
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "Column"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        }
        if columns:
            helpers[function.name] = columns
    return helpers


def test_create_table_constraints_only_reference_declared_columns() -> None:
    """Catch migration defects before an empty-database upgrade reaches PostgreSQL."""
    failures: list[str] = []

    for migration_path in sorted(VERSIONS_DIR.glob("*.py")):
        tree = ast.parse(migration_path.read_text(), filename=str(migration_path))
        column_helpers = _column_helpers(tree)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "op"
                and call.func.attr == "create_table"
                and call.args
                and isinstance(call.args[0], ast.Constant)
            ):
                continue

            table_name = str(call.args[0].value)
            declared: set[str] = set()
            referenced: set[str] = set()
            for argument in call.args[1:]:
                if (
                    isinstance(argument, ast.Starred)
                    and isinstance(argument.value, ast.Call)
                    and isinstance(argument.value.func, ast.Name)
                    and argument.value.func.id in column_helpers
                ):
                    declared.update(column_helpers[argument.value.func.id])
                    continue
                if not isinstance(argument, ast.Call) or not isinstance(argument.func, ast.Attribute):
                    if (
                        isinstance(argument, ast.Call)
                        and isinstance(argument.func, ast.Name)
                        and argument.func.id == "_business_fk"
                    ):
                        referenced.add("business_id")
                    continue
                kind = argument.func.attr
                if kind == "Column" and argument.args and isinstance(argument.args[0], ast.Constant):
                    declared.add(str(argument.args[0].value))
                elif kind in {"PrimaryKeyConstraint", "UniqueConstraint"}:
                    referenced.update(_string_arguments(argument))
                elif kind == "ForeignKeyConstraint" and argument.args:
                    referenced.update(_string_list(argument.args[0]))

            missing = sorted(referenced - declared)
            if missing:
                failures.append(f"{migration_path.name}:{table_name} missing {missing}")

    assert failures == []
