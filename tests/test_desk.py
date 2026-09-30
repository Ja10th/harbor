import os
import tempfile
import unittest

from harbor import db, draft, mailer, net
from harbor.discover import assign_tiers, company_name
from harbor.research import extract_contacts, pick_fact, social_from_url


READY = {
    "sender_name": "Ada Okonkwo",
    "sender_company": "Oak Press",
    "sender_role": "Publisher",
    "from_email": "ada@oakpress.com",
    "reply_to": "",
    "postal_address": "12 Broad Street, Lagos",
    "website": "https://oakpress.com",
    "offer": "I publish practical guides for small shops.",
    "daily_cap": 20,
}


class DeskTests(unittest.TestCase):
    def test_public_url_blocks_metadata_and_odd_ports(self):
        self.assertFalse(net.is_public_url("http://169.254.169.254/"))
        self.assertFalse(net.is_public_url("http://127.0.0.1/"))
        self.assertFalse(net.is_public_url("file:///etc/passwd"))
        self.assertFalse(net.is_public_url("https://paystack.com:8080/about"))
        self.assertTrue(net.is_public_url("https://paystack.com/about"))

    def test_pick_fact_skips_cookie_banner(self):
        pages = [{
            "text": "Accept all cookies. Paystack helps businesses in Nigeria accept payments online.",
            "own": True,
        }]
        fact = pick_fact("Paystack", pages)
        self.assertIn("accept payments", fact)

    def test_extracts_published_email_and_business_social(self):
        html = """
        <a href="mailto:hello@paystack.com">email</a>
        <a href="https://instagram.com/paystack">ig</a>
        <a href="https://instagram.com/p/ABC123">post</a>
        <a href="https://www.linkedin.com/in/shola">person</a>
        <a href="mailto:noreply@paystack.com">no</a>
        """
        found = extract_contacts(html, "https://paystack.com")
        emails = [item["email"] for item in found["emails"]]
        self.assertIn("hello@paystack.com", emails)
        self.assertNotIn("noreply@paystack.com", emails)
        self.assertEqual(found["socials"][0]["network"], "instagram")
        self.assertEqual(found["socials"][0]["handle"], "paystack")
        self.assertIsNone(social_from_url("https://www.linkedin.com/in/shola"))
        self.assertIsNone(social_from_url("https://instagram.com/p/ABC123"))

    def test_company_search_groups_without_fake_rank(self):
        self.assertEqual(company_name("Julius Berger Nigeria | Construction", "construction company"), "Julius Berger Nigeria")
        self.assertEqual(company_name("Top 10 construction companies in Lagos", "construction company"), "")
        grouped = assign_tiers([{"company": f"Firm {i}", "score": 6 - i} for i in range(6)])
        self.assertEqual([item["tier"] for item in grouped], ["top", "top", "middle", "middle", "thin", "thin"])

    def test_letter_relates_offer_to_a_page_it_read(self):
        letter = draft.build_first(
            {
                "name": "Julius Berger Nigeria",
                "company": "Julius Berger Nigeria",
                "seen_post": "",
                "recent": {
                    "text": "The company completed a road project in Lagos.",
                    "url": "https://julius-berger.com/projects",
                    "how": "page",
                    "domain": "julius-berger.com",
                },
            },
            "",
            {**READY, "offer": "I set up CRM and automations for construction firms."},
        )
        self.assertIn("Saw this on your projects page", letter["body"])
        self.assertIn("falls behind the site", letter["body"])
        self.assertIn("CRM and automations", letter["body"])
        self.assertNotIn("about a project", letter["body"])
        self.assertNotIn("I wrote because", letter["body"])
        self.assertNotIn("following", letter["body"].lower())
        self.assertTrue(letter["body"].startswith("Hello,"))

    def test_letter_cites_a_real_post_without_fake_history(self):
        letter = draft.build_first(
            {
                "name": "Chioma Ade",
                "company": "Paystack",
                "seen_post": "Their March photo of the Lagos office.",
            },
            "Paystack helps businesses accept payments online.",
            READY,
        )
        self.assertIn("Saw this on your post", letter["body"])
        self.assertIn("Lagos office", letter["body"])
        self.assertNotIn("following", letter["body"].lower())
        self.assertTrue(mailer.claims_fake_history("We've been following you for so long"))

    def test_letter_does_not_fake_a_reply(self):
        letter = draft.build_first(
            {"name": "Chioma Ade", "company": "Paystack"},
            "Paystack helps businesses accept payments online.",
            READY,
        )
        self.assertFalse(letter["subject"].lower().startswith("re:"))
        self.assertIn("accept payments", letter["body"])
        self.assertNotIn("Replace this sentence", letter["body"])

    def test_footer_carries_address_and_unsubscribe(self):
        text = mailer.footer(READY)
        self.assertIn("12 Broad Street, Lagos", text)
        self.assertIn("unsubscribe", text.lower())

    def test_ready_letter_can_send(self):
        contact = {
            "name": "Chioma Ade",
            "email": "chioma@shop.com",
            "company": "Shop",
            "reason": "She asked for a sample after a talk.",
            "status": "approved",
            "first_sent_at": "",
        }
        letter = {
            "kind": "first",
            "status": "approved",
            "subject": "Note for Shop",
            "body": "Hi Chioma,\n\nI read this about Shop: they sell cloth.\n\nI publish guides.",
        }
        blocks = mailer.send_blockers(
            settings=READY,
            contact=contact,
            draft=letter,
            smtp_configured=True,
            sent_today=0,
            seconds_since_last=30,
            first_sent_at="",
            followup_wait_over=False,
        )
        self.assertEqual(blocks, [])

    def test_practice_address_and_lure_are_blocked(self):
        contact = {
            "name": "Practice Contact",
            "email": "practice@example.com",
            "company": "Paystack",
            "reason": "Practice row so I can see a draft.",
            "status": "approved",
            "first_sent_at": "",
        }
        letter = {
            "kind": "first",
            "status": "approved",
            "subject": "Verify your account password",
            "body": "Please verify your account password.",
        }
        blocks = mailer.send_blockers(
            settings=READY,
            contact=contact,
            draft=letter,
            smtp_configured=True,
            sent_today=0,
            seconds_since_last=30,
            first_sent_at="",
            followup_wait_over=False,
        )
        self.assertTrue(any("Practice" in item for item in blocks))
        self.assertTrue(any("lure" in item for item in blocks))

    def test_parse_csv_accepts_semicolons(self):
        rows = db.parse_csv("name;email;company\nAda;ada@example.com;Paystack\n")
        self.assertEqual(rows[0]["email"], "ada@example.com")
        self.assertEqual(rows[0]["company"], "Paystack")

    def test_import_roundtrip(self):
        previous = os.environ.get("HARBOR_DATA")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["HARBOR_DATA"] = tmp
                db.close()
                db.init()
                result = db.import_csv(
                    "name,email,company,reason\nAda,ada@example.com,Paystack,Met at a book fair.\n"
                )
                self.assertEqual(result["added"], 1)
                people = db.list_contacts()
                self.assertEqual(people[0]["email"], "ada@example.com")
                db.close()
        finally:
            if previous is None:
                os.environ.pop("HARBOR_DATA", None)
            else:
                os.environ["HARBOR_DATA"] = previous
            db.close()


if __name__ == "__main__":
    unittest.main()
