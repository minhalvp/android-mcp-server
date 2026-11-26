"""What is on the screen: a UI Automator dump turned into elements with refs, rendered as compact text."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Literal, NamedTuple

Role = Literal["button", "edit", "switch", "checkbox", "radio", "dropdown", "tab", "list", "item"]
# Position of a node in the UI tree: its index at each level from the root.
Path = tuple[int, ...]

# In display order.
FLAGS = ("checked", "unchecked", "password", "focused", "selected", "disabled", "scroll", "long-click", "off-screen")

# Widget class names contain the name of the widget they extend (ToggleButton,
# AppCompatCheckBox, MaterialSwitch), so the more specific names come first.
_ROLES_BY_CLASS: tuple[tuple[str, Role], ...] = (
    ("Switch", "switch"),
    ("ToggleButton", "switch"),
    ("CheckBox", "checkbox"),
    ("RadioButton", "radio"),
    ("Spinner", "dropdown"),
    ("Button", "button"),
    ("Tab", "tab"),
)
_ACTION_ATTRIBUTES = ("clickable", "long-clickable", "scrollable", "checkable")
_EDIT_CLASSES = {"EditText", "AutoCompleteTextView", "MultiAutoCompleteTextView"}
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]")
_MAX_DESCENDANT_LABELS = 6
_MAX_LABEL = 80


class RefError(Exception):
    """A ref can't be acted on: it was never issued, or the screen changed since."""


class Point(NamedTuple):
    x: int
    y: int


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> Point:
        return Point((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def clip(self, other: "Bounds") -> "Bounds | None":
        clipped = Bounds(
            max(self.left, other.left),
            max(self.top, other.top),
            min(self.right, other.right),
            min(self.bottom, other.bottom),
        )
        return clipped if clipped.width > 0 and clipped.height > 0 else None


@dataclass(frozen=True)
class Element:
    """Something the user can act on."""

    ref: str
    path: Path
    role: Role
    # Leaves out the text of a field Android marks as a password.
    label: str
    # The visible part of the element, or None when it is off-screen.
    bounds: Bounds | None
    flags: tuple[str, ...]
    # Equal for the same element in a later dump, even after typing into it.
    identity: tuple[str, ...]
    text: str

    def __str__(self) -> str:
        flags = f" [{', '.join(self.flags)}]" if self.flags else ""
        return f"{self.ref} {self.role} {_quote(self.label)}{flags}"


@dataclass(frozen=True)
class Snapshot:
    screen: Bounds
    rotation: int
    # Every actionable element in tree order, including off-screen ones.
    elements: tuple[Element, ...]
    # Visible text that isn't already part of an element's label.
    texts: tuple[tuple[Path, str], ...]

    @classmethod
    def parse(cls, xml_text: str) -> "Snapshot":
        """Every actionable element gets a ref (e1, e2, ...) in tree order, on screen or not."""
        root = ET.fromstring(xml_text)
        nodes = list(_walk(root, ()))
        # Android's first node describes the screen area.
        screen = (_bounds(nodes[0][0]) if nodes else None) or Bounds(0, 0, 0, 0)

        elements: list[Element] = []
        texts: list[tuple[Path, str]] = []
        labelled_by_descendants: list[Path] = []
        for node, path in nodes:
            raw = _bounds(node)
            visible = raw.clip(screen) if raw else None
            if _is_actionable(node):
                label, borrowed = _label(node)
                elements.append(_element(node, path, visible, f"e{len(elements) + 1}", label))
                if borrowed:
                    labelled_by_descendants.append(path)
            elif visible and not _inside_any(path, labelled_by_descendants) and (text := _own_label(node)):
                texts.append((path, text))
        return cls(screen, int(root.get("rotation", "0")), tuple(elements), tuple(texts))

    def render(self, include_offscreen: bool = False) -> str:
        """One line per element in screen order, e.g. ``e2 switch "Wi-Fi" [checked]``."""
        shown = [e for e in self.elements if include_offscreen or e.bounds]
        lines = [(e.path, str(e)) for e in shown] + [(path, f"text {_quote(t)}") for path, t in self.texts]
        out = [f"screen {self.screen.width}x{self.screen.height}" + (f" rot{self.rotation}" if self.rotation else "")]
        out += [line for _, line in sorted(lines)]
        if not shown:
            out.append("(no actionable elements)")
        if hidden := len(self.elements) - len(shown):
            out.append(f"({hidden} off-screen elements omitted; snapshot with include_offscreen=true lists them)")
        return "\n".join(out)

    def locate(self, ref: str, live: "Snapshot") -> tuple[Element, Bounds]:
        """The element behind one of this snapshot's refs, as it is in live, with its visible bounds.

        Clocks and animations change the screen constantly, so rather than
        demanding an identical screen, the element only has to keep its
        identity (class, resource ID and label). It is looked for at its old
        tree path first; elements also move in the tree (a keyboard opens, a
        list re-sorts), so failing that, it is used wherever it is now, as long
        as exactly one element has that identity.
        """
        issued = next((e for e in self.elements if e.ref == ref), None)
        if issued is None:
            raise RefError(f"unknown ref {ref!r}; call snapshot for current refs")
        current = next((e for e in live.elements if e.path == issued.path and e.identity == issued.identity), None)
        if current is None:
            matches = [e for e in live.elements if e.identity == issued.identity]
            if len(matches) > 1:
                raise RefError(
                    f"{ref} ({issued.label!r}) moved and {len(matches)} elements now match it; call snapshot again"
                )
            if not matches:
                raise RefError(f"{ref} ({issued.label!r}) changed or is gone; call snapshot again")
            current = matches[0]
        if current.bounds is None:
            raise RefError(f"{ref} ({issued.label!r}) is off-screen; swipe to bring it into view")
        return current, current.bounds


EMPTY = Snapshot(Bounds(0, 0, 0, 0), 0, (), ())


def _is_actionable(node: ET.Element) -> bool:
    return _is_editable(node) or any(node.get(name) == "true" for name in _ACTION_ATTRIBUTES)


def _element(node: ET.Element, path: Path, visible: Bounds | None, ref: str, label: str) -> Element:
    on = {name: node.get(name) == "true" for name in _ACTION_ATTRIBUTES}
    editable = _is_editable(node)
    role = _role(node, editable, on["scrollable"])
    checked = node.get("checked") == "true"
    state = {
        "checked": on["checkable"] and checked,
        "unchecked": on["checkable"] and not checked,
        "password": _is_password(node),
        "focused": node.get("focused") == "true",
        "selected": node.get("selected") == "true",
        "disabled": node.get("enabled") != "true",
        "scroll": on["scrollable"] and role != "list",
        "long-click": on["long-clickable"],
        "off-screen": visible is None,
    }
    # An edit field's text is its value, which typing changes, so it can't identify the field.
    shown = node.get("content-desc", "").strip() if editable else label
    return Element(
        ref=ref,
        path=path,
        role=role,
        label=label,
        bounds=visible,
        flags=tuple(flag for flag in FLAGS if state[flag]),
        identity=(node.get("class", ""), node.get("resource-id", ""), shown),
        text=node.get("text", ""),
    )


def _role(node: ET.Element, editable: bool, scrollable: bool) -> Role:
    if editable:
        return "edit"
    short_class = _short_class(node)
    for fragment, role in _ROLES_BY_CLASS:
        if fragment in short_class:
            return role
    return "list" if scrollable else "item"


def _label(node: ET.Element) -> tuple[str, bool]:
    """The node's own text, else its children's, else its resource ID or class; and whether children's was used."""
    if own := _own_label(node):
        return own, False
    # Scroll-only containers can hold a whole page, so only clickable ones borrow their children's text.
    if node.get("clickable") == "true":
        children: list[str] = []
        for descendant in list(node.iter("node"))[1:]:
            label = _own_label(descendant)
            if label and label not in children:
                children.append(label)
            if len(children) == _MAX_DESCENDANT_LABELS:
                break
        if children:
            return ", ".join(children), True
    if resource_id := node.get("resource-id", ""):
        return resource_id.rsplit("/", 1)[-1].replace("_", " ").strip() or resource_id, False
    return _short_class(node) or "unlabelled element", False


def _own_label(node: ET.Element) -> str | None:
    """What the node itself says. A password field's text is its secret, not its label."""
    text = "" if _is_password(node) else node.get("text", "").strip()
    description = node.get("content-desc", "").strip()
    if text and description and text != description:
        return f"{text} ({description})"
    return text or description or None


def _quote(value: str) -> str:
    value = " ".join(value.split())
    if len(value) > _MAX_LABEL:
        value = value[: _MAX_LABEL - 1] + "…"
    return '"' + value.replace('"', "'") + '"'


def _bounds(node: ET.Element) -> Bounds | None:
    """Android's ``[left,top][right,bottom]`` format."""
    values = [int(n) for pair in _BOUNDS_RE.findall(node.get("bounds", "")) for n in pair]
    return Bounds(*values) if len(values) == 4 else None


def _is_editable(node: ET.Element) -> bool:
    return node.get("editable") == "true" or _short_class(node) in _EDIT_CLASSES


def _is_password(node: ET.Element) -> bool:
    return node.get("password") == "true"


def _short_class(node: ET.Element) -> str:
    return node.get("class", "").rsplit(".", 1)[-1]


def _inside_any(path: Path, ancestors: list[Path]) -> bool:
    return any(len(path) > len(a) and path[: len(a)] == a for a in ancestors)


def _walk(parent: ET.Element, parent_path: Path):
    """Every node with its path, parents before their children."""
    for position, node in enumerate(parent.findall("node")):
        # uiautomator leaves invisible children out of the dump; its index
        # still counts them, so paths stay stable when they appear.
        index = node.get("index", "")
        path = (*parent_path, int(index) if index.isdigit() else position)
        yield node, path
        yield from _walk(node, path)
