"""Navigation groups, route context and progressive mobile rendering."""
from html.parser import HTMLParser

from django.test import Client
from django.urls import reverse

from care.models import ActionReceipt, Activity, AuditEvent, Product

from .test_functional import CareCase


class Markup(HTMLParser):
    """Inspect rendered links, including their containing navigation landmark."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack), "text": ""}
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        for node in self.stack:
            node["text"] += text

    def select(self, *, tag=None, class_name=None, parent=None, **attrs):
        return [
            node for node in self.nodes
            if (tag is None or node["tag"] == tag)
            and (class_name is None or class_name in node["attrs"].get("class", "").split())
            and (parent is None or any(ancestor is parent for ancestor in node["parents"]))
            and all(node["attrs"].get(key) == value for key, value in attrs.items())
        ]


GROUPS = {
    "care": ("돌봄", (("modules", "기능"), ("routines", "루틴"), ("supplies", "물품"))),
    "history": ("기록", (("records", "기록"), ("overview", "통계"))),
    "manage": ("관리", (("members", "구성원"), ("room_settings", "반려동물"), ("preferences", "설정"))),
}


class NavigationTests(CareCase):
    def page(self, name, *args, layout="mobile", client=None):
        response = (client or self.client).get(
            reverse(name, args=[self.room.pk, *args]), {"layout": layout}
        )
        self.assertEqual(response.status_code, 200, name)
        return response, Markup(response.content.decode())

    def dock(self, markup, scope):
        docks = markup.select(tag="nav", class_name="mobile-dock")
        self.assertEqual(len(docks), 1)
        dock = docks[0]
        self.assertEqual(dock["attrs"].get("data-nav-scope"), scope)
        expected_class = "is-global" if scope == "global" else "is-context"
        self.assertIn(expected_class, dock["attrs"].get("class", "").split())
        return dock

    def assert_scope(self, markup, scope, current_name):
        dock = self.dock(markup, scope)
        links = markup.select(tag="a", class_name="dock-link", parent=dock)
        expected = GROUPS[scope][1]
        self.assertEqual(
            [(link["attrs"].get("href"), link["text"].strip()) for link in links],
            [(reverse(name, args=[self.room.pk]), label) for name, label in expected],
        )
        current = [link for link in links if link["attrs"].get("aria-current") == "page"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["attrs"]["href"], reverse(current_name, args=[self.room.pk]))
        back = markup.select(tag="a", parent=dock, **{"aria-label": "기본 메뉴로 돌아가기"})
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]["attrs"]["href"], reverse("today", args=[self.room.pk]))

    def test_today_has_global_navigation_and_one_current_destination(self):
        _, markup = self.page("today")
        dock = self.dock(markup, "global")
        links = markup.select(tag="a", class_name="dock-link", parent=dock)
        self.assertEqual(
            [(link["attrs"].get("href"), link["text"].strip()) for link in links],
            [(reverse(name, args=[self.room.pk]), label) for name, label in (
                ("today", "오늘"), ("modules", "돌봄"), ("records", "기록"),
                ("members", "관리"), ("navigation", "전체"),
            )],
        )
        current = markup.select(tag="a", parent=dock, **{"aria-current": "page"})
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["attrs"]["href"], reverse("today", args=[self.room.pk]))

    def test_each_existing_destination_selects_its_functional_scope(self):
        for scope, (_, destinations) in GROUPS.items():
            for name, _ in destinations:
                with self.subTest(scope=scope, destination=name):
                    _, markup = self.page(name)
                    self.assert_scope(markup, scope, name)

    def test_nested_pages_keep_their_parent_navigation(self):
        record = self.make("water")
        routes = (
            ("module", ("water",), "care", "modules"),
            ("record_new", ("water",), "care", "modules"),
            ("record_edit", (record.pk,), "care", "modules"),
            ("record_detail", (record.pk,), "history", "records"),
            ("statistics", ("water",), "history", "overview"),
            ("routine_new", (), "care", "routines"),
            ("product_new", (), "care", "supplies"),
            ("member_panel", (self.owner.pk,), "manage", "members"),
            ("space_management", (), "manage", "preferences"),
        )
        for name, args, scope, current in routes:
            with self.subTest(destination=name):
                _, markup = self.page(name, *args)
                self.assert_scope(markup, scope, current)

    def test_validation_error_keeps_record_navigation(self):
        record = self.make("water")
        before = record.version
        url = reverse("record_cancel", args=[self.room.pk, record.pk]) + "?layout=mobile"
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 400)
        self.assert_scope(Markup(response.content.decode()), "history", "records")
        record.refresh_from_db()
        self.assertEqual(record.version, before)

    def test_desktop_sidebar_retains_original_order_and_assets(self):
        routes = ("today", "modules", "records", "routines", "supplies", "overview")
        for route in (*routes, "members", "room_settings", "preferences"):
            with self.subTest(route=route):
                response, markup = self.page(route, layout="desktop")
                sidebar = markup.select(tag="aside", id="sidebar")[0]
                primary = markup.select(tag="nav", parent=sidebar, **{"aria-label": "돌봄 탐색"})[0]
                self.assertEqual(
                    [link["attrs"]["href"] for link in markup.select(tag="a", parent=primary)],
                    [reverse(name, args=[self.room.pk]) for name in routes],
                )
                self.assertTrue(markup.select(class_name="sidebar-members", parent=sidebar))
                self.assertTrue(markup.select(class_name="sidebar-settings", parent=sidebar))
                self.assertFalse(markup.select(class_name="sidebar-nav-group"))
                self.assertNotContains(response, "care/navigation.css")
                self.assertNotContains(response, "care/mobile-menu.js")
                dock = markup.select(tag="nav", class_name="mobile-dock")[0]
                self.assertNotIn("data-nav-scope", dock["attrs"])
                self.assertEqual(
                    [link["text"].strip() for link in markup.select(tag="a", parent=dock)],
                    ["오늘", "기능", "기록", "루틴", "통계"],
                )

    def test_navigation_hub_exposes_real_links_for_every_destination(self):
        for layout in ("mobile",):
            with self.subTest(layout=layout):
                _, markup = self.page("navigation", layout=layout)
                self.dock(markup, "global")
                main = markup.select(tag="main", id="main")[0]
                hrefs = {node["attrs"].get("href") for node in markup.select(tag="a", parent=main)}
                for _, destinations in GROUPS.values():
                    for name, _ in destinations:
                        self.assertIn(reverse(name, args=[self.room.pk]), hrefs)
                dock = markup.select(tag="nav", class_name="mobile-dock")[0]
                current = markup.select(tag="a", parent=dock, **{"aria-current": "page"})
                self.assertEqual(len(current), 1)
                self.assertEqual(current[0]["attrs"]["href"], reverse("navigation", args=[self.room.pk]))

    def test_full_menu_is_mobile_only(self):
        url = reverse("navigation", args=[self.room.pk])
        self.assertRedirects(self.client.get(url, {"layout": "desktop"}),
                             reverse("today", args=[self.room.pk]), fetch_redirect_response=False)

    def test_mobile_menu_trigger_is_a_link_when_scripts_are_unavailable(self):
        _, markup = self.page("today")
        triggers = markup.select(tag="a", class_name="phone-menu-trigger")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["attrs"]["href"], reverse("navigation", args=[self.room.pk]))

    def test_explicit_layout_and_auto_mode_survive_navigation(self):
        url = reverse("today", args=[self.room.pk])
        mobile_headers = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone) Mobile"}
        response = self.client.get(url, **mobile_headers)
        self.assertTemplateUsed(response, "care/layout_mobile.html")
        response = self.client.get(url, {"layout": "desktop"}, **mobile_headers)
        self.assertTemplateUsed(response, "care/layout_desktop.html")
        self.assertEqual(response.cookies["petnote_layout"].value, "desktop")
        response = self.client.get(reverse("records", args=[self.room.pk]), **mobile_headers)
        self.assertTemplateUsed(response, "care/layout_desktop.html")
        response = self.client.get(url, {"layout": "auto"}, **mobile_headers)
        self.assertTemplateUsed(response, "care/layout_mobile.html")
        self.assertEqual(response.cookies["petnote_layout"]["max-age"], 0)
        self.client.cookies.pop("petnote_layout", None)
        response = self.client.get(url, HTTP_USER_AGENT="Mozilla/5.0 Desktop")
        self.assertTemplateUsed(response, "care/layout_desktop.html")

    def test_client_hint_selects_mobile_and_explicit_mobile_persists(self):
        url = reverse("today", args=[self.room.pk])
        response = self.client.get(url, HTTP_SEC_CH_UA_MOBILE="?1")
        self.assertTemplateUsed(response, "care/layout_mobile.html")
        response = self.client.get(url, {"layout": "mobile"}, HTTP_USER_AGENT="Desktop")
        self.assertTemplateUsed(response, "care/layout_mobile.html")
        response = self.client.get(reverse("records", args=[self.room.pk]), HTTP_USER_AGENT="Desktop")
        self.assertTemplateUsed(response, "care/layout_mobile.html")

    def test_guests_reach_shared_navigation_without_gaining_edit_rights(self):
        guest = self.client_for(self.guest)
        for name, scope in (("navigation", "global"), ("members", "manage"),
                            ("room_settings", "manage"), ("preferences", "manage"),
                            ("supplies", "care")):
            with self.subTest(destination=name):
                response, markup = self.page(name, client=guest)
                self.dock(markup, scope)
                if name == "room_settings":
                    self.assertTrue(response.context["read_only"])
        self.assertEqual(guest.get(reverse("product_new", args=[self.room.pk])).status_code, 403)
        self.assertEqual(guest.get(reverse("space_management", args=[self.room.pk])).status_code, 403)
        self.assertEqual(guest.get(reverse("module_settings", args=[self.room.pk])).status_code, 403)

    def test_hub_denies_other_accounts_and_removed_members(self):
        outsider = Client()
        outsider.force_login(self.outsider_user)
        url = reverse("navigation", args=[self.room.pk])
        self.assertEqual(outsider.get(url).status_code, 404)
        guest = self.client_for(self.guest)
        self.guest.active = False
        self.guest.save(update_fields=["active"])
        self.assertEqual(guest.get(url).status_code, 404)

    def test_navigation_gets_do_not_change_care_data(self):
        models = (Activity, AuditEvent, ActionReceipt, Product)
        before = tuple(model.objects.count() for model in models)
        revision = self.room.revision
        version = self.room.settings_version
        for name in ("navigation", "today", "modules", "records", "routines", "overview",
                     "supplies", "members", "room_settings", "preferences"):
            self.page(name)
        self.room.refresh_from_db()
        self.assertEqual(tuple(model.objects.count() for model in models), before)
        self.assertEqual((self.room.revision, self.room.settings_version), (revision, version))
