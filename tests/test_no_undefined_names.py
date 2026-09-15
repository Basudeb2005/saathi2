"""Every name every function uses has to exist.

This file exists because of a shipped NameError. A function was renamed
from `_keyboard` to `_ptt`, one call site in `run_forever` was missed,
and `WAKE_MODE=space` died on its first line — on a Pi, in front of
someone trying to use it. Nothing caught it: the tests exercise the
pure pieces, and `run_forever` is the part that can only really be run
on hardware, so the broken line was never executed here.

Python will not tell you about this until the line runs. The symbol
tables will, without importing anything — which matters, because half
this package imports livekit and openai and the point is to check the
code, not the environment it needs.

It is a small piece of what pyflakes does, kept in-tree rather than
added as a dependency, because this is the one check that has actually
caught something.
"""
import builtins
import pathlib
import symtable

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "saathi"
BUILTINS = set(dir(builtins))

# Names Python puts in a module without anyone writing them.
IMPLICIT = {"__file__", "__name__", "__doc__", "__package__", "__spec__",
            "__loader__", "__builtins__", "__path__", "__annotations__",
            "__class__", "__debug__", "__module__", "__qualname__"}


def modules():
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def module_level_names(table: symtable.SymbolTable) -> set:
    """What the module actually binds: assignments, imports, defs, classes."""
    names = set()
    for symbol in table.get_symbols():
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace():
            names.add(symbol.get_name())
    return names


def globals_used(table: symtable.SymbolTable, path=()):
    """Every global name each nested scope reads, with where it read it.

    is_global() is the key: a name a function reads but never binds, and
    which isn't in an enclosing function either. A lazy `from x import y`
    inside a function binds y locally, so it isn't one of these — which
    is what makes this safe on a codebase built around lazy imports.
    """
    for symbol in table.get_symbols():
        if symbol.is_global() and symbol.is_referenced():
            yield symbol.get_name(), " -> ".join(path + (table.get_name(),))
    for child in table.get_children():
        yield from globals_used(child, path + (table.get_name(),))


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_every_name_a_function_uses_exists(path):
    source = path.read_text()
    top = symtable.symtable(source, str(path), "exec")
    defined = module_level_names(top) | BUILTINS | IMPLICIT

    missing = sorted({
        f"{name} (used in {where})"
        for name, where in globals_used(top)
        if name not in defined
    })
    assert not missing, (
        f"{path.relative_to(PACKAGE.parent)} uses names that don't exist:\n  "
        + "\n  ".join(missing)
    )
