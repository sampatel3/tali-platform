"""
Workable ATS integration service.

Handles posting assessment results and updating candidate pipeline
stages via the Workable API (v3).
"""

import logging

import httpx

logger = logging.getLogger(__name__)


class WorkableService:
    """Service for interacting with the Workable applicant tracking system."""

    def __init__(self, access_token: str, subdomain: str):
        """
        Initialise the Workable service.

        Args:
            access_token: Workable API bearer token.
            subdomain: Organisation subdomain on Workable (e.g. 'acme').
        """
        self.base_url = f"https://{subdomain}.workable.com/spi/v3"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        logger.info(
            "WorkableService initialised (subdomain=%s, base_url=%s)",
            subdomain,
            self.base_url,
        )

    def post_assessment_result(
        self, candidate_id: str, assessment_data: dict
    ) -> dict:
        """
        Post an assessment result as a candidate activity in Workable.

        Args:
            candidate_id: The Workable candidate ID.
            assessment_data: Dict containing score, tests_passed, tests_total,
                             time_taken, and results_url.

        Returns:
            Dict with keys: success, response.
        """
        try:
            score = assessment_data.get("score", 0)
            tests_passed = assessment_data.get("tests_passed", 0)
            tests_total = assessment_data.get("tests_total", 0)
            time_taken = assessment_data.get("time_taken", "N/A")
            results_url = assessment_data.get("results_url", "")

            body = {
                "body": (
                    "TAALI Assessment Complete\n\n"
                    f"Overall score: {score}/10\n"
                    f"Tests passed: {tests_passed}/{tests_total}\n"
                    f"Time taken: {time_taken} minutes\n"
                    f"Full recruiter report: {results_url}\n\n"
                    "This result was posted automatically by TAALI."
                ),
            }

            url = f"{self.base_url}/candidates/{candidate_id}/activities"
            logger.info(
                "Posting assessment result for candidate %s (score=%s)",
                candidate_id,
                score,
            )

            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=body, headers=self.headers)
                response.raise_for_status()

            logger.info(
                "Assessment result posted successfully for candidate %s",
                candidate_id,
            )

            return {
                "success": True,
                "response": response.json(),
            }
        except httpx.HTTPStatusError as e:
            logger.error(
                "Workable API error posting result for candidate %s: %s (status=%d)",
                candidate_id,
                str(e),
                e.response.status_code,
            )
            return {
                "success": False,
                "response": {"error": str(e), "status_code": e.response.status_code},
            }
        except Exception as e:
            logger.error(
                "Failed to post assessment result for candidate %s: %s",
                candidate_id,
                str(e),
            )
            return {
                "success": False,
                "response": {"error": str(e)},
            }

    def update_candidate_stage(self, candidate_id: str, stage: str) -> dict:
        """
        Update a candidate's pipeline stage in Workable.

        Args:
            candidate_id: The Workable candidate ID.
            stage: The target stage name to move the candidate to.

        Returns:
            Dict with keys: success, response.
        """
        try:
            url = f"{self.base_url}/candidates/{candidate_id}"
            payload = {"stage": stage}

            logger.info(
                "Updating candidate %s to stage '%s'", candidate_id, stage
            )

            with httpx.Client(timeout=30.0) as client:
                response = client.patch(url, json=payload, headers=self.headers)
                response.raise_for_status()

            logger.info(
                "Candidate %s stage updated to '%s' successfully",
                candidate_id,
                stage,
            )

            return {
                "success": True,
                "response": response.json(),
            }
        except httpx.HTTPStatusError as e:
            logger.error(
                "Workable API error updating stage for candidate %s: %s (status=%d)",
                candidate_id,
                str(e),
                e.response.status_code,
            )
            return {
                "success": False,
                "response": {"error": str(e), "status_code": e.response.status_code},
            }
        except Exception as e:
            logger.error(
                "Failed to update stage for candidate %s: %s",
                candidate_id,
                str(e),
            )
            return {
                "success": False,
                "response": {"error": str(e)},
            }
