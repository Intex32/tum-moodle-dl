"""Find HTML forms on a page and fill them in the way a browser would submit them."""

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

TEXT_TYPES = {"text", "password", "email", "number", "tel", "search", "url"}


@dataclass(frozen=True)
class Input:
    """A visible field the user would type into."""

    name: str
    type: str
    id: str
    autocomplete: str


@dataclass(frozen=True)
class Button:
    name: str
    value: str
    label: str


@dataclass(frozen=True)
class Form:
    action: str
    method: str
    fields: list[tuple[str, str]]  # what the browser sends without any user input, in page order
    inputs: list[Input]
    buttons: list[Button]  # named submit buttons, in page order
    checkboxes: list[str]  # names, ticked or not

    def has(self, name: str) -> bool:
        return any(n == name for n, _ in self.fields) or any(b.name == name for b in self.buttons)

    def value(self, name: str) -> str | None:
        return next((v for n, v in self.fields if n == name), None)

    def button(self, name: str) -> Button | None:
        return next((b for b in self.buttons if b.name == name), None)

    def submission(
        self, values: dict[str, str | None] | None = None, button: str | None = None
    ) -> list[tuple[str, str]]:
        """Form data with ``values`` filled in (``None`` leaves a field out), sent via ``button``.

        Without ``button`` the first submit button is used, like pressing Enter does.
        """
        values = values or {}
        data: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, value in self.fields:
            if name not in values:
                data.append((name, value))
            elif name not in seen:
                seen.add(name)
                if values[name] is not None:
                    data.append((name, values[name]))
        data += [(n, v) for n, v in values.items() if n not in seen and v is not None]
        pressed = self.button(button) if button else next(iter(self.buttons), None)
        if pressed and pressed.name not in values:
            data.append((pressed.name, pressed.value))
        return data


def post_data(data: list[tuple[str, str]]) -> dict[str, str | list[str]]:
    """httpx wants repeated fields as lists; the order within a name is kept."""
    result: dict[str, str | list[str]] = {}
    for name, value in data:
        if name in result:
            existing = result[name]
            result[name] = [*existing, value] if isinstance(existing, list) else [existing, value]
        else:
            result[name] = value
    return result


def parse_forms(html: str, base_url: str) -> list[Form]:
    soup = BeautifulSoup(html, "html.parser")
    forms = []
    for form in soup.find_all("form"):
        fields: list[tuple[str, str]] = []
        inputs: list[Input] = []
        buttons: list[Button] = []
        checkboxes: list[str] = []
        for el in form.find_all(["input", "button", "select", "textarea"]):
            name = el.get("name")
            if not name or el.has_attr("disabled"):
                continue
            if el.name == "button":
                if (el.get("type") or "submit").lower() == "submit":
                    label = " ".join(el.get_text(" ").split())
                    buttons.append(Button(name, el.get("value", ""), label))
            elif el.name == "select":
                options = el.find_all("option")
                selected = [o for o in options if o.has_attr("selected")] or options[:1]
                if not el.has_attr("multiple"):
                    selected = selected[:1]
                fields += [(name, o.get("value", o.get_text())) for o in selected]
            elif el.name == "textarea":
                fields.append((name, el.get_text()))
            else:
                kind = (el.get("type") or "text").lower()
                value = el.get("value", "")
                if kind in ("submit", "image"):
                    buttons.append(Button(name, value, value))
                elif kind in ("checkbox", "radio"):
                    if kind == "checkbox":
                        checkboxes.append(name)
                    if el.has_attr("checked"):
                        fields.append((name, el.get("value", "on")))
                elif kind not in ("reset", "button", "file"):
                    fields.append((name, value))
                    if kind in TEXT_TYPES:
                        inputs.append(
                            Input(name, kind, el.get("id", ""), el.get("autocomplete", ""))
                        )
        action = urljoin(base_url, form.get("action") or base_url)
        method = (form.get("method") or "get").upper()
        forms.append(Form(action, method, fields, inputs, buttons, checkboxes))
    return forms


def error_text(html: str) -> str | None:
    """The first error message shown on a page, if any."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select('[role=alert], .alert-danger, [class*="error"]'):
        if el.name in ("html", "body"):
            continue
        text = " ".join(el.get_text(" ").split())
        if text:
            return text[:300]
    return None


def page_title(html: str) -> str:
    title = BeautifulSoup(html, "html.parser").title
    return " ".join(title.get_text().split()) if title else ""
