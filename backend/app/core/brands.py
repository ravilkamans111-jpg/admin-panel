"""Static registry of known brands.

The three source monoliths have no `Brand`/`Tenant` model at all — each is a
separate deployment of the same codebase against its own database (see the
migration research reports). This list is therefore new: it is what lets the
JWT's `brand_id` claim resolve to an actual tenant DB via
`app.core.config.get_brand_db_config`.

Kept as a static list rather than a DB table for now — it changes rarely
(a new brand = a new deployment + a new Vault path), and control-plane
`BrandAccess` rows reference these ids by string, not by FK, so adding a
brand here plus its Vault secret is enough to onboard it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrandInfo:
    brand_id: str
    display_name: str


KNOWN_BRANDS: dict[str, BrandInfo] = {
    "ampay": BrandInfo(brand_id="ampay", display_name="AmPay"),
    "rajapay": BrandInfo(brand_id="rajapay", display_name="RajaPay"),
    "quiet-forest": BrandInfo(brand_id="quiet-forest", display_name="Quiet Forest"),
}


def is_known_brand(brand_id: str) -> bool:
    return brand_id in KNOWN_BRANDS
