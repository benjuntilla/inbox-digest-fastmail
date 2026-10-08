"""Unit tests for the internal-forwarder guard in mail_actions.

The bug we're pinning: an "unsubscribe" attempt on a message routed through a
shared org forwarder pointed the List-Unsubscribe header at the group's
manage-subscription page — following it would remove you from your own
forwarder. The guard refuses these links by inspecting the header value for
your own org domains (account.ORG_DOMAINS).
"""

from __future__ import annotations

import pytest

from email_review import contacts_files, mail_actions
from email_review.account import ORG_DOMAINS
from email_review.mail_actions import _is_internal_forwarder_unsub

# The org domain configured in account.py.
_ORG = ORG_DOMAINS[0]


class TestInternalForwarderGuard:
    """Refuse List-Unsubscribe links that point back at your own domains."""

    def test_org_groups_url_is_blocked(self):
        # The classic internal-forwarder incident: a group's subscribe page.
        header = f"<https://groups.google.com/a/{_ORG}/group/ap/subscribe>"
        assert _is_internal_forwarder_unsub(header)

    def test_org_mailto_is_blocked(self):
        header = f"<mailto:ap+unsubscribe@{_ORG}>"
        assert _is_internal_forwarder_unsub(header)

    def test_substack_url_is_allowed(self):
        # Real third-party newsletters should pass through.
        header = (
            "<https://substack.com/api/v1/email/notification/unsubscribe?token=xyz>"
        )
        assert not _is_internal_forwarder_unsub(header)

    def test_external_groups_google_url_is_allowed(self):
        # A Google Group hosted on someone else's Workspace must NOT trigger
        # the guard — your org domain isn't present.
        header = "<https://groups.google.com/a/example.com/group/list/subscribe>"
        assert not _is_internal_forwarder_unsub(header)

    def test_empty_header_is_not_treated_as_internal(self):
        assert not _is_internal_forwarder_unsub("")

    def test_url_with_org_domain_in_query_param_is_blocked(self):
        # Defensive: even if your org domain only appears as a parameter,
        # refuse. Better to over-refuse than to lose the forwarder again.
        header = f"<https://example.com/unsub?target=ap@{_ORG}>"
        assert _is_internal_forwarder_unsub(header)


class TestUnsubscribeNeedsConfirmation:
    """The smart action never unsubscribes on the first call: it asks."""

    @pytest.fixture
    def acted(self, monkeypatch, tmp_path):
        calls = {"modify": [], "unsub": []}
        monkeypatch.setattr(contacts_files, "APP_CONTACTS_PATH", tmp_path / "app_contacts.txt")
        monkeypatch.setattr(contacts_files, "HAND_CONTACTS_PATH", tmp_path / "hand_contacts.txt")
        monkeypatch.setattr(
            mail_actions,
            "get_message_headers",
            lambda mid: {"list-unsubscribe": "<https://lists.example/unsub?u=1>", "list-unsubscribe-post": "List-Unsubscribe=One-Click"},
        )
        monkeypatch.setattr(mail_actions, "modify_thread", lambda tid, add, remove: calls["modify"].append((add, remove)))
        monkeypatch.setattr(mail_actions, "try_unsubscribe", lambda mid, tid: (calls["unsub"].append(mid), (True, "HTTP"))[1])
        return mail_actions, calls

    def test_first_call_asks_and_changes_nothing(self, acted):
        mail_actions, calls = acted
        r = mail_actions.smart_action("m1", "t1", "7", sender="News <news@lists.example>")
        assert r["action"] == "needs_confirm"
        assert r["sender_addr"] == "news@lists.example"
        assert r["sender_name"] == "News"
        assert calls == {"modify": [], "unsub": []}

    def test_confirmed_call_unsubscribes(self, acted):
        mail_actions, calls = acted
        r = mail_actions.smart_action("m1", "t1", "7", sender="News <news@lists.example>", confirm_unsubscribe=True)
        assert r["action"] == "unsubscribed"
        assert calls["unsub"] == ["m1"]

    def test_protected_sender_is_archived_not_asked(self, acted):
        mail_actions, calls = acted
        assert mail_actions.add_keep_subscribed("news@lists.example", "News") is True
        assert mail_actions.add_keep_subscribed("NEWS@lists.example") is False  # already protected
        r = mail_actions.smart_action("m1", "t1", "7", sender="News <news@lists.example>")
        assert r["action"] == "archived"
        assert calls["unsub"] == []
        text = contacts_files.APP_CONTACTS_PATH.read_text()
        assert "news@lists.example\tNews\tkeep-subscribed" in text

    def test_protected_row_lands_above_imported_block(self, acted):
        mail_actions, _ = acted
        contacts_files.APP_CONTACTS_PATH.write_text("# header\n\n# ---- BEGIN Fastmail contacts (managed)\nx@y.example\tX\ttrusted-warm\tn\n# ---- END Fastmail contacts ----\n")
        mail_actions.add_keep_subscribed("a@b.example", "A")
        text = contacts_files.APP_CONTACTS_PATH.read_text()
        assert text.index("a@b.example") < text.index("# ---- BEGIN Fastmail contacts")

    def test_not_an_address_is_refused(self, acted):
        mail_actions, _ = acted
        with pytest.raises(mail_actions.NotAnEmailAddress):
            mail_actions.add_keep_subscribed("nobody")


def test_rows_read_both_files_and_skip_comments(tmp_path, monkeypatch):
    hand = tmp_path / "hand.txt"
    app = tmp_path / "app.txt"
    hand.write_text("# c\nA@x.example, b@x.example\tA\tvendor\tn\nbad line\n")
    app.write_text("c@y.example\tC\tkeep-subscribed\tn\n")
    rows = list(contacts_files.rows((hand, app, tmp_path / "missing.txt")))
    assert rows == [({"a@x.example", "b@x.example"}, "A", "vendor"), ({"c@y.example"}, "C", "keep-subscribed")]
