"""Spec2Test - requirements to reviewable test drafts.

Layering (enforced by tests/test_architecture.py):
    domain       - pure data model and rules, no I/O of any kind
    application  - use cases, depends on domain + ports only
    adapters     - HTTP, documents, fakes; implement the ports
    ui           - existing PySide6 views (unchanged in L1)
"""

__version__ = "0.2.0"
