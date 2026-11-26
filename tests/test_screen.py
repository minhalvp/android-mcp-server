"""Parsing a UI dump, rendering it, and resolving refs against a later dump."""

from pathlib import Path

import pytest

from android_pilot.screen import Bounds, RefError, Snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "settings_wifi.xml"

UI_DUMP = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="1">
  <node index="0" bounds="[0,0][400,800]" class="android.widget.FrameLayout">
    <node index="0" clickable="true" enabled="true" text="Login" content-desc="Open login"
          resource-id="app:id/login" class="android.widget.Button" bounds="[0,100][200,150]" focused="true"/>
    <node index="1" clickable="true" enabled="true" resource-id="app:id/city_clock"
          class="android.widget.LinearLayout" bounds="[20,200][220,400]">
      <node index="0" text="London" class="android.widget.TextView" bounds="[30,220][150,260]"/>
      <node index="1" text="09:00" content-desc="09:00" class="android.widget.TextView" bounds="[30,270][150,320]"/>
    </node>
    <node index="2" clickable="true" enabled="false" resource-id="app:id/empty_action"
          class="android.view.View" bounds="[0,0][0,0]"/>
    <node index="3" text="Monday 1 Jan" class="android.widget.TextView" bounds="[10,20][180,60]"/>
    <node index="4" scrollable="true" long-clickable="true" checkable="true" checked="true" enabled="true"
          class="android.widget.ListView" bounds="[0,400][400,700]">
      <node index="0" text="List item" class="android.widget.TextView" bounds="[20,420][200,470]"/>
    </node>
  </node>
</hierarchy>
"""


def page(body: str, rotation: int = 0) -> str:
    return f'<hierarchy rotation="{rotation}"><node bounds="[0,0][400,800]">{body}</node></hierarchy>'


def button(label: str = "Login", y: int = 100, extra: str = 'enabled="true"', cls: str = "Button") -> str:
    return (
        f'<node index="0" clickable="true" text="{label}" resource-id="app:id/go" class="android.widget.{cls}" '
        f'bounds="[0,{y}][200,{y + 50}]" {extra}/>'
    )


def password_field(value: str) -> str:
    return page(
        f'<node index="0" enabled="true" password="true" text="{value}" resource-id="app:id/pin" '
        'class="android.widget.EditText" bounds="[0,100][200,150]"/>'
    )


def lines(xml: str, include_offscreen: bool = False) -> list[str]:
    return Snapshot.parse(xml).render(include_offscreen).splitlines()


class TestRender:
    def test_real_screen(self):
        assert lines(FIXTURE.read_text()) == [
            "screen 1080x2400",
            'text "Network & internet"',
            'e1 switch "Wi-Fi (Wi-Fi is on)" [checked]',
            'e2 item "Internet"',
            'e3 edit "Search settings"',
            'e4 list "recycler view"',
            'text "SIMs"',
        ]

    def test_every_kind_of_element(self):
        assert lines(UI_DUMP, include_offscreen=True) == [
            "screen 400x800 rot1",
            'e1 button "Login (Open login)" [focused]',
            'e2 item "London, 09:00"',
            'e3 item "empty action" [disabled, off-screen]',
            'text "Monday 1 Jan"',
            'e4 list "ListView" [checked, long-click]',
            'text "List item"',
        ]

    def test_only_state_that_differs_from_the_default_is_shown(self):
        assert (
            lines(page(button(extra='enabled="false" selected="true"')))[1] == 'e1 button "Login" [selected, disabled]'
        )
        assert lines(page(button(extra='enabled="true" checkable="true"', cls="Switch")))[1] == (
            'e1 switch "Login" [unchecked]'
        )

    def test_scroll_is_flagged_where_the_role_does_not_imply_it(self):
        assert lines(page(button(extra='enabled="true" scrollable="true"')))[1] == 'e1 button "Login" [scroll]'

    def test_offscreen_elements_are_counted_not_listed(self):
        assert lines(page(button("Shown") + button("Hidden", y=900)))[1:] == [
            'e1 button "Shown"',
            "(1 off-screen elements omitted; snapshot with include_offscreen=true lists them)",
        ]

    def test_empty_screen_says_so(self):
        assert lines(page("")) == ["screen 400x800", "(no actionable elements)"]

    def test_labels_are_truncated_and_cannot_break_out_of_quotes(self):
        assert lines(page(button("x" * 200)))[1] == f'e1 button "{"x" * 79}…"'
        assert lines(page(button("say &quot;hi&quot;")))[1] == "e1 button \"say 'hi'\""


class TestParse:
    def test_partly_visible_element_is_clipped_to_the_screen(self):
        [element] = Snapshot.parse(page(button(y=780))).elements
        assert element.bounds == Bounds(0, 780, 200, 800)

    def test_text_fields_are_edits_without_a_clickable_flag(self):
        xml = page('<node text="abc" class="android.widget.AutoCompleteTextView" bounds="[0,0][100,50]"/>')
        assert Snapshot.parse(xml).elements[0].role == "edit"

    def test_password_text_is_never_a_label(self):
        xml = page(
            '<node index="0" clickable="true" enabled="true" class="android.widget.LinearLayout" '
            'bounds="[0,0][200,200]">'
            '<node index="0" password="true" text="hunter2" resource-id="app:id/pin" '
            'class="android.widget.EditText" bounds="[10,20][190,80]"/></node>'
        )
        rendered = Snapshot.parse(xml).render()

        assert "hunter2" not in rendered
        assert 'edit "pin" [password' in rendered


class TestLocate:
    def locate(self, issued: str, live: str, ref: str = "e1"):
        return Snapshot.parse(issued).locate(ref, Snapshot.parse(live))

    def test_uses_the_live_position(self):
        _, bounds = self.locate(page(button(y=100)), page(button(y=300)))
        assert bounds.center == (100, 325)

    @pytest.mark.parametrize(
        ("live", "message"),
        [
            (page(""), "changed or is gone"),
            (page(button("Logout")), "changed or is gone"),
            (page(button(y=900)), "off-screen"),
        ],
    )
    def test_refuses_when_the_element_changed_or_left(self, live, message):
        with pytest.raises(RefError, match=message):
            self.locate(page(button()), live)

    def test_follows_an_element_that_moved_in_the_tree(self):
        # A keyboard opening or a list re-sorting moves elements to a new tree path.
        moved = page(
            '<node index="0" class="android.widget.FrameLayout" bounds="[0,0][400,800]"/>'
            + button(y=300).replace('index="0"', 'index="1"')
        )
        element, bounds = self.locate(page(button(y=100)), moved)
        assert element.path == (0, 1)
        assert bounds.center == (100, 325)

    def test_refuses_when_a_moved_element_matches_more_than_one(self):
        two = page(button(y=300).replace('index="0"', 'index="1"') + button(y=500).replace('index="0"', 'index="2"'))
        with pytest.raises(RefError, match="2 elements now match"):
            self.locate(page(button()), two)

    def test_prefers_the_same_path_when_duplicates_exist(self):
        two = page(button(y=300) + button(y=500).replace('index="0"', 'index="1"'))
        element, _ = self.locate(page(button()), two)
        assert element.path == (0, 0)

    def test_refuses_unknown_refs(self):
        with pytest.raises(RefError, match="unknown ref"):
            self.locate(page(button()), page(button()), ref="e9")

    def test_edit_field_survives_typing_into_it(self):
        def field(value: str) -> str:
            return page(f'<node index="0" text="{value}" class="android.widget.EditText" bounds="[0,0][100,50]"/>')

        element, _ = self.locate(field("ab"), field("abc"))
        assert element.text == "abc"

    def test_password_field_survives_and_is_never_echoed(self):
        element, _ = self.locate(password_field("hunter2"), password_field("hunter3"))
        assert element.label == "pin"

        with pytest.raises(RefError) as error:
            self.locate(password_field("hunter2"), page(""))
        assert "hunter2" not in str(error.value)
