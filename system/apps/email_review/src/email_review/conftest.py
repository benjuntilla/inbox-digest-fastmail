"""Pin the account identity the tests are written against.

account.py holds the real user's identity, but the classifier and phishing
fixtures are written for the template's fixed placeholder identity ("Alex Doe"
at yourcompany.example). Patch the module before any test imports code that
copies these constants at import time, so the tests do not depend on whose
inbox this is.
"""

from __future__ import annotations

from email_review import account

account.ACCOUNT_NAME = "Alex Doe"
account.ACCOUNT_FIRST_NAME = "Alex"
account.ACCOUNT_ADDRS = {"alex@yourcompany.example", "alex.doe@gmail.example"}
account.ORG_DOMAINS = ("yourcompany.example",)
account.AP_FORWARDER_ADDRS = ("ap@yourcompany.example",)
account.SCHOOL_DOMAINS = ("university.example",)
