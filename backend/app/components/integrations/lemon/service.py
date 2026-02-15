from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class LemonService:
    def __init__(self, *, api_key: str, store_id: str):
        self.api_key = api_key
        self.store_id = store_id
        self.base_url = "https://api.lemonsqueezy.com/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }

    def create_checkout(
        self,
        *,
        variant_id: str,
        success_url: str,
        cancel_url: str,
        email: str,
        custom: dict[str, Any],
        test_mode: bool = False,
    ) -> str:
        payload = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "checkout_data": {
                        "email": email,
                        "custom": custom,
                    },
                    "product_options": {
                        "redirect_url": success_url,
                    },
                    "checkout_options": {
                        "skip_trial": True,
                    },
                    "expires_at": None,
                    "preview": False,
                    "test_mode": bool(test_mode),
                },
                "relationships": {
                    "store": {"data": {"type": "stores", "id": str(self.store_id)}},
                    "variant": {"data": {"type": "variants", "id": str(variant_id)}},
                },
            }
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post(f"{self.base_url}/checkouts", json=payload, headers=self.headers)
        if response.status_code >= 400:
            logger.error("Lemon checkout create failed: %s", response.text)
            response.raise_for_status()
        body = response.json() or {}
        checkout_url = (
            body.get("data", {})
            .get("attributes", {})
            .get("url")
        )
        if not checkout_url:
            raise ValueError("Lemon checkout URL missing")
        if cancel_url:
            separator = "&" if "?" in checkout_url else "?"
            checkout_url = f"{checkout_url}{separator}checkout[cancel_url]={cancel_url}"
        return checkout_url

    @staticmethod
    def verify_signature(*, payload: bytes, signature: str, secret: str) -> bool:
        if not signature or not secret:
            return False
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
