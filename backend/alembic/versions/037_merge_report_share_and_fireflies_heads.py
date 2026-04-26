"""Merge report-share and Fireflies invite email heads.

Revision ID: 037_merge_report_share_and_fireflies_heads
Revises: 036_add_application_report_share_links, 036_add_fireflies_invite_email
Create Date: 2026-04-25
"""

from __future__ import annotations


revision = "037_merge_report_share_and_fireflies_heads"
down_revision = ("036_add_application_report_share_links", "036_add_fireflies_invite_email")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
