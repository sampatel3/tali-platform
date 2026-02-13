"""
Stripe payment service for assessment billing and subscriptions.

Handles customer creation, one-off assessment charges, and recurring
subscription management via the Stripe API.
"""

import logging

import stripe

from ....platform.config import settings

logger = logging.getLogger(__name__)


class StripeService:
    """Service for managing payments and subscriptions through Stripe."""

    def __init__(self, api_key: str):
        """
        Initialise the Stripe service.

        Args:
            api_key: Stripe secret API key.
        """
        stripe.api_key = api_key
        logger.info("StripeService initialised")

    def create_customer(self, email: str, name: str) -> dict:
        """
        Create a new Stripe customer.

        Args:
            email: Customer email address.
            name: Customer display name.

        Returns:
            Dict with keys: success, customer_id.
        """
        try:
            logger.info("Creating Stripe customer (email=%s)", email)

            customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={"platform": "tali"},
            )

            logger.info(
                "Stripe customer created successfully (customer_id=%s)", customer.id
            )

            return {
                "success": True,
                "customer_id": customer.id,
            }
        except stripe.StripeError as e:
            logger.error("Stripe error creating customer: %s", str(e))
            return {
                "success": False,
                "customer_id": "",
            }
        except Exception as e:
            logger.error("Unexpected error creating Stripe customer: %s", str(e))
            return {
                "success": False,
                "customer_id": "",
            }

    def charge_assessment(self, customer_id: str, amount: int | None = None) -> dict:
        """
        Create a PaymentIntent to charge for a single assessment.

        Args:
            customer_id: Stripe customer ID.
            amount: Charge amount in minor units (default from settings).

        Returns:
            Dict with keys: success, payment_intent_id.
        """
        try:
            amount_minor = int(amount if amount is not None else (settings.ASSESSMENT_PRICE_MINOR or 2500))
            currency_code = (settings.ASSESSMENT_PRICE_CURRENCY or "aed").lower()
            logger.info(
                "Creating assessment charge (customer_id=%s, amount=%d %s-minor-units)",
                customer_id,
                amount_minor,
                currency_code,
            )

            payment_intent = stripe.PaymentIntent.create(
                amount=amount_minor,
                currency=currency_code,
                customer=customer_id,
                description="TALI Assessment Fee",
                metadata={"type": "assessment"},
            )

            logger.info(
                "PaymentIntent created successfully (id=%s)", payment_intent.id
            )

            return {
                "success": True,
                "payment_intent_id": payment_intent.id,
            }
        except stripe.StripeError as e:
            logger.error("Stripe error creating assessment charge: %s", str(e))
            return {
                "success": False,
                "payment_intent_id": "",
            }
        except Exception as e:
            logger.error(
                "Unexpected error creating assessment charge: %s", str(e)
            )
            return {
                "success": False,
                "payment_intent_id": "",
            }

    def create_subscription(
        self, customer_id: str, price_id: str = "price_monthly_300"
    ) -> dict:
        """
        Create a recurring subscription for a customer.

        Args:
            customer_id: Stripe customer ID.
            price_id: Stripe Price ID for the plan (default: monthly plan).

        Returns:
            Dict with keys: success, subscription_id.
        """
        try:
            logger.info(
                "Creating subscription (customer_id=%s, price_id=%s)",
                customer_id,
                price_id,
            )

            subscription = stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": price_id}],
                metadata={"platform": "tali"},
            )

            logger.info(
                "Subscription created successfully (id=%s)", subscription.id
            )

            return {
                "success": True,
                "subscription_id": subscription.id,
            }
        except stripe.StripeError as e:
            logger.error("Stripe error creating subscription: %s", str(e))
            return {
                "success": False,
                "subscription_id": "",
            }
        except Exception as e:
            logger.error(
                "Unexpected error creating subscription: %s", str(e)
            )
            return {
                "success": False,
                "subscription_id": "",
            }
