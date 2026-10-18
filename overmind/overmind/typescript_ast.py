"""TypeScript/JavaScript counterpart of the Python AST scan: function inventory, call graph, literal prompts.

Qualnames follow the Python convention so both languages share the chassis checks:
``Class.method``, ``outer.<locals>.inner``, and ``object.member`` for functions held by a named object
literal. A variable initialised by a call that wraps a function (``const run = withRetry(async () => ...)``)
is one function named by the variable; a class with a constructor is callable under its own name.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable, Iterator
from functools import cache
from typing import TYPE_CHECKING

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

if TYPE_CHECKING:
    from overmind.chassis import Chassis

# A repo that checks in a bundle is not describing its own code.
MAX_SOURCE_BYTES = 2_000_000
_BUNDLE_SUFFIXES = (".min.js", ".min.mjs", ".min.cjs", ".bundle.js")

Emit = Callable[[str, str, list[Node]], None]

_FUNCTION_NODES = frozenset({
    "arrow_function",
    "function_expression",
    "function",
    "generator_function",
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
})
_FUNCTION_DECLARATIONS = frozenset({"function_declaration", "generator_function_declaration"})
_CLASS_NODES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_FIELD_NODES = frozenset({"public_field_definition", "field_definition"})
_TRANSPARENT = frozenset({
    "parenthesized_expression",
    "as_expression",
    "satisfies_expression",
    "non_null_expression",
})
_DEFINITION_NODES = frozenset({
    "function_declaration",
    "generator_function_declaration",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
    "import_statement",
})
_CODE_LINE_RE = re.compile(
    r"^\s*(?:import |export |const |let |var |function |async function |class |interface |type |return |if |else\b|"
    r"for |while |try\b|catch\b|switch )",
    re.MULTILINE,
)
_MIN_NESTED_PROMPT_CHARS = 40
_STRIPPING_METHODS = frozenset({"trim", "trimStart", "trimEnd"})
_DEDENT_TAGS = frozenset({"dedent", "outdent", "stripIndent"})
_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_ESCAPE_RE = re.compile(r"\\(?:u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(\r\n|\n|\r)|(.))", re.DOTALL)


@cache
def _parser(grammar: str) -> Parser:
    language = {
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
        "javascript": tree_sitter_javascript.language,
    }[grammar]
    return Parser(Language(language()))


def _grammar_for(path: str) -> str:
    lowered = path.lower().split("#", 1)[0]
    if lowered.endswith((".ts", ".mts", ".cts")):
        return "typescript"
    if lowered.endswith(".tsx"):
        return "tsx"
    return "javascript"


def is_bundle(path: str) -> bool:
    return path.lower().endswith(_BUNDLE_SUFFIXES)


def parse(source: bytes, path: str) -> Node:
    return _parser(_grammar_for(path)).parse(source).root_node


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text is not None else ""


def _unwrap(node: Node) -> Node:
    while node.type in _TRANSPARENT and node.named_children:
        node = node.named_children[0]
    return node


def _is_function(node: Node) -> bool:
    return node.type in _FUNCTION_NODES


def _has_function(node: Node) -> bool:
    stack = list(node.named_children)
    while stack:
        current = stack.pop()
        if current.type in _FUNCTION_NODES:
            return True
        stack.extend(current.named_children)
    return False


def _key_name(node: Node | None) -> str:
    if node is None or node.type == "computed_property_name":
        return ""
    return _text(node).strip("\"'`")


def _callee_name(node: Node | None) -> str:
    if node is None:
        return ""
    node = _unwrap(node)
    if node.type == "identifier":
        return _text(node)
    if node.type == "member_expression":
        return _text(node.child_by_field_name("property"))
    return ""


def called_names(roots: list[Node]) -> set[str]:
    """Names a function can invoke: callees, constructors, and identifiers handed on as arguments or
    object values (handlers and tool registries run later). Over-approximate by name, like the Python chassis."""
    called: set[str] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in ("call_expression", "new_expression"):
            called.add(
                _callee_name(node.child_by_field_name("function" if kind == "call_expression" else "constructor"))
            )
            arguments = node.child_by_field_name("arguments")
            if arguments is not None and arguments.type == "arguments":
                called.update(_callee_name(argument) for argument in arguments.named_children)
        elif kind == "pair":
            called.add(_callee_name(node.child_by_field_name("value")))
        elif kind == "shorthand_property_identifier":
            called.add(_text(node))
        elif kind == "extends_clause":
            called.add(_callee_name(node.child_by_field_name("value")))
        elif kind == "class_heritage":
            called.update(_callee_name(base) for base in node.named_children)
        elif kind in ("jsx_self_closing_element", "jsx_opening_element"):
            tag = _text(node.child_by_field_name("name"))
            if tag[:1].isupper():
                called.add(tag.rsplit(".", 1)[-1])
        stack.extend(node.named_children)
    called.discard("")
    return called


def _function(node: Node, name: str, prefix: str, emit: Emit) -> None:
    qualname = f"{prefix}{name}"
    emit(qualname, "function", [node])
    body = node.child_by_field_name("body")
    if body is not None:
        _collect(body, f"{qualname}.<locals>.", emit)


def _class(node: Node, name: str, prefix: str, emit: Emit) -> None:
    qualname = f"{prefix}{name}"
    # `new Name()` runs the constructor, the field initialisers and the base constructor.
    construction: list[Node] = [child for child in node.named_children if child.type == "class_heritage"]
    body = node.child_by_field_name("body")
    for member in body.named_children if body is not None else []:
        if member.type == "method_definition":
            member_name = _key_name(member.child_by_field_name("name"))
            if not member_name:
                _collect(member, f"{qualname}.", emit)
                continue
            _function(member, member_name, f"{qualname}.", emit)
            if member_name == "constructor":
                construction.append(member)
        elif member.type in _FIELD_NODES:
            member_name = _key_name(member.child_by_field_name("name") or member.child_by_field_name("property"))
            value = member.child_by_field_name("value")
            inner = _unwrap(value) if value is not None else None
            if inner is not None and member_name and _is_function(inner):
                _function(inner, member_name, f"{qualname}.", emit)
            elif inner is not None:
                construction.append(inner)
                _collect(inner, f"{qualname}.", emit)
        else:
            _collect(member, f"{qualname}.", emit)
    emit(qualname, "class", construction)


def _object(node: Node, prefix: str, emit: Emit) -> None:
    for member in node.named_children:
        if member.type == "method_definition":
            name = _key_name(member.child_by_field_name("name"))
            if name:
                _function(member, name, prefix, emit)
            else:
                _collect(member, prefix, emit)
        elif member.type == "pair":
            name = _key_name(member.child_by_field_name("key"))
            value = member.child_by_field_name("value")
            if value is None:
                continue
            if not name or not _declare_value(_unwrap(value), name, prefix, emit):
                _collect(value, prefix, emit)
        else:
            _collect(member, prefix, emit)


def _declare_value(value: Node, name: str, prefix: str, emit: Emit) -> bool:
    """Name ``value`` as ``prefix + name`` when it is, or wraps, a function."""
    if _is_function(value):
        _function(value, name, prefix, emit)
    elif value.type in _CLASS_NODES:
        _class(value, name, prefix, emit)
    elif value.type == "object":
        _object(value, f"{prefix}{name}.", emit)
    elif _has_function(value):
        emit(f"{prefix}{name}", "function", [value])
        _collect(value, f"{prefix}{name}.<locals>.", emit)
    else:
        return False
    return True


def _assigned_name(target: Node) -> str:
    name = _text(target)
    for noise in ("module.exports", "exports", "this"):
        if name == noise:
            return ""
        name = name.removeprefix(f"{noise}.")
    return name.replace(".prototype", "")


def _declare(node: Node, prefix: str, emit: Emit) -> bool:
    """Handle a node that introduces named scopes; ``False`` leaves it to the generic descent."""
    kind = node.type
    if kind in _FUNCTION_DECLARATIONS or kind in _CLASS_NODES:
        name = _text(node.child_by_field_name("name"))
        if not name:
            return False
        (_function if kind in _FUNCTION_DECLARATIONS else _class)(node, name, prefix, emit)
        return True
    if kind == "variable_declarator":
        target, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        if target is None or value is None or target.type != "identifier":
            return False
        return _declare_value(_unwrap(value), _text(target), prefix, emit)
    if kind == "assignment_expression":
        target, value = node.child_by_field_name("left"), node.child_by_field_name("right")
        if target is None or value is None or target.type not in ("identifier", "member_expression"):
            return False
        name = _assigned_name(target)
        inner = _unwrap(value)
        if inner.type == "object" and not name:
            _object(inner, prefix, emit)
            return True
        return _declare_value(inner, name or "default", prefix, emit)
    if kind == "export_statement" and any(child.type == "default" for child in node.children):
        values = [child for child in node.named_children if child.type != "decorator"]
        # A default export that carries its own name is declared by that name, not as ``default``.
        if not values or _named(values[-1]):
            return False
        return _declare_value(_unwrap(values[-1]), "default", prefix, emit)
    if kind in ("internal_module", "module"):
        name = _key_name(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        if name and body is not None:
            _collect(body, f"{prefix}{name}.", emit)
            return True
    return False


def _named(node: Node) -> bool:
    return node.type in _FUNCTION_DECLARATIONS | _CLASS_NODES and bool(_text(node.child_by_field_name("name")))


def _collect(root: Node, prefix: str, emit: Emit) -> None:
    stack = [root]
    while stack:
        node = stack.pop()
        if not _declare(node, prefix, emit):
            stack.extend(reversed(node.named_children))


def iter_definitions(root: Node) -> Iterator[tuple[str, str, list[Node]]]:
    """``(qualname, kind, call roots)`` for each function and class, kind ``function`` or ``class``."""
    found: list[tuple[str, str, list[Node]]] = []
    _collect(root, "", lambda qualname, kind, roots: found.append((qualname, kind, roots)))
    return iter(found)


def source_qualnames(source: str, path: str) -> set[str]:
    return {qualname for qualname, _, _ in iter_definitions(parse(source.encode("utf-8"), path))}


def add_functions(source: bytes, rel_path: str, module: str, chassis: Chassis) -> None:
    for qualname, kind, roots in iter_definitions(parse(source, rel_path)):
        # A class is callable only when constructing it runs code.
        if kind == "class" and not roots:
            continue
        full = f"{module}.{qualname}" if module else qualname
        chassis.functions[full] = rel_path
        chassis.calls[full] = called_names(roots)


def _decode_escape(match: re.Match[str]) -> str:
    braced, fixed, hexpair, newline, other = match.groups()
    if braced or fixed or hexpair:
        try:
            return chr(int(braced or fixed or hexpair, 16))
        except ValueError:
            return match.group(0)
    if newline:
        return ""
    return _SIMPLE_ESCAPES.get(other, other)


def _literal_text(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in _TRANSPARENT:
        return _literal_text(node.named_children[0]) if node.named_children else None
    if node.type in ("string", "template_string"):
        parts: list[str] = []
        for child in node.children:
            if child.type == "string_fragment":
                parts.append(_text(child))
            elif child.type == "escape_sequence":
                parts.append(_ESCAPE_RE.sub(_decode_escape, _text(child)))
            elif child.type == "template_substitution":
                parts.append("{" + _text(child.named_children[0]).strip() + "}" if child.named_children else "{...}")
        return "".join(parts)
    if node.type == "binary_expression" and _text(node.child_by_field_name("operator")) == "+":
        left = _literal_text(node.child_by_field_name("left"))
        right = _literal_text(node.child_by_field_name("right"))
        return None if left is None or right is None else left + right
    if node.type == "call_expression":
        function = _unwrap(node.child_by_field_name("function") or node)
        arguments = node.child_by_field_name("arguments")
        if arguments is not None and arguments.type == "template_string":
            tag = _callee_name(function)
            inner = _literal_text(arguments)
            if inner is None or tag not in _DEDENT_TAGS | {"raw"}:
                return None
            return textwrap.dedent(inner).strip() if tag in _DEDENT_TAGS else inner
        if function.type == "member_expression" and _callee_name(function) in _STRIPPING_METHODS:
            inner = _literal_text(function.child_by_field_name("object"))
            return None if inner is None else inner.strip()
    return None


def _statement_value(node: Node) -> Node | None:
    if node.type == "expression_statement":
        return node.named_children[0] if node.named_children else None
    if node.type in ("lexical_declaration", "variable_declaration"):
        declarator = next((child for child in node.named_children if child.type == "variable_declarator"), None)
        return declarator.child_by_field_name("value") if declarator is not None else None
    if node.type == "export_statement":
        inner = [child for child in node.named_children if child.type != "decorator"]
        if not inner:
            return None
        if inner[-1].type in ("lexical_declaration", "variable_declaration"):
            return _statement_value(inner[-1])
        return inner[-1]
    return None


def _defines(node: Node) -> bool:
    if node.type in _DEFINITION_NODES:
        return True
    if node.type == "export_statement":
        return any(_defines(child) or _is_function(child) for child in node.named_children)
    if node.type in ("lexical_declaration", "variable_declaration"):
        return any(
            (value := declarator.child_by_field_name("value")) is not None
            and (_is_function(_unwrap(value)) or _unwrap(value).type in _CLASS_NODES)
            for declarator in node.named_children
            if declarator.type == "variable_declarator"
        )
    return False


def prompt_text_from_source(source: str, path: str) -> str:
    """The literal prompt a cited span holds: the first top-level string value, else the longest nested
    literal. Spans that define code yield nothing; prose that is not code is the prompt itself."""
    text = textwrap.dedent(source).strip()
    if not text:
        return ""
    encoded = text.encode("utf-8")
    root = parse(encoded, path)
    if root.has_error:
        return "" if _CODE_LINE_RE.search(text) else text
    if any(_defines(node) for node in root.named_children):
        return ""
    for node in root.named_children:
        value = _statement_value(node)
        literal = _literal_text(value) if value is not None else None
        if literal and literal.strip():
            return literal.strip()
    nested: list[str] = []
    stack = list(root.named_children)
    while stack:
        node = stack.pop()
        if node.type in ("string", "template_string"):
            found = _literal_text(node)
            if found is not None and len(found.strip()) >= _MIN_NESTED_PROMPT_CHARS:
                nested.append(found.strip())
        stack.extend(node.named_children)
    return max(nested, key=len) if nested else ""
