"""Preflight - check before you sign.

A read-only precheck for mint/claim contracts: deployment, source
verification, price, supply, open status, wallet eligibility, eth_call
simulation and live gas cost, rolled into one verdict -
CLEAR, CAUTION or BLOCKED - with itemised reasons.

It never signs, never approves, never sends a transaction and never asks
for a key. If it cannot resolve or verify something, it says so.
"""

__version__ = "0.1.2"
