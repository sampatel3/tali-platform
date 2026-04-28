"""One-shot Sonnet generator for archetype rubrics (RALPH 2.6).

Run manually when adding a new archetype. Reads N anonymised JDs,
calls Sonnet 4.6 with the prompt in ``_generation_prompt.md``, validates
the output against ``ArchetypeRubric``, and writes the YAML.

NOT auto-invoked from any runtime path. Production never calls Sonnet
during a CV match — only this generator does.

Usage:

    from app.cv_matching.rubrics._generate import generate_archetype
    generate_archetype(
        archetype_id="genai_engineer",
        anonymised_jd_paths=[
            "tools/sample_jds/genai_engineer_1.txt",
            "tools/sample_jds/genai_engineer_2.txt",
        ],
        out_path="backend/app/cv_matching/rubrics/genai_engineer.yaml",
    )

The function refuses to overwrite an existing YAML — delete the file
manually if you want to regenerate.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

import yaml

from .schema import ArchetypeRubric

logger = logging.getLogger("taali.cv_match.rubrics.generate")

# Sonnet 4.6 (NOT Haiku) — quality matters here, cost is negligible.
_GENERATOR_MODEL = "claude-sonnet-4-6"
_GENERATOR_TEMPERATURE = 0.0
_GENERATOR_MAX_TOKENS = 4000


def _read_prompt_template() -> str:
    """Read the prompt template from _generation_prompt.md.

    The README has the full thing inside a fenced block. We extract
    that block at runtime so the prompt and the docs stay in sync.
    """
    md_path = Path(__file__).resolve().parent / "_generation_prompt.md"
    text = md_path.read_text(encoding="utf-8")
    # The first triple-backtick block is the prompt body.
    in_block = False
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("```"):
            if in_block:
                break
            in_block = True
            continue
        if in_block:
            out.append(line)
    if not out:
        raise RuntimeError(
            "Could not find a fenced prompt block in _generation_prompt.md"
        )
    return "\n".join(out)


def generate_archetype(
    *,
    archetype_id: str,
    anonymised_jd_paths: Sequence[str | Path],
    out_path: str | Path,
    client=None,
) -> ArchetypeRubric:
    """Call Sonnet to produce one archetype rubric YAML.

    Refuses to overwrite. Returns the parsed ``ArchetypeRubric`` so
    the caller can inspect it programmatically before reviewing.
    """
    out = Path(out_path)
    if out.exists():
        raise FileExistsError(
            f"{out} already exists. Delete it manually to regenerate."
        )

    jd_texts = [Path(p).read_text(encoding="utf-8") for p in anonymised_jd_paths]
    if not 1 <= len(jd_texts) <= 5:
        raise ValueError("Pass between 1 and 5 anonymised JDs")

    prompt_template = _read_prompt_template()
    jd_block = "\n".join(
        f"<JD_{i + 1}>\n{txt}\n</JD_{i + 1}>" for i, txt in enumerate(jd_texts)
    )
    prompt = prompt_template.replace(
        "Anonymised JDs follow:\n\n<JD_1>\n{jd_1_text}\n</JD_1>\n\n<JD_2>\n{jd_2_text}\n</JD_2>\n\n<JD_3>\n{jd_3_text}\n</JD_3>",
        f"Anonymised JDs follow:\n\n{jd_block}",
    )

    if client is None:
        from ..runner import _resolve_anthropic_client

        client = _resolve_anthropic_client()

    logger.info(
        "Calling Sonnet for archetype=%s with %d JDs", archetype_id, len(jd_texts)
    )
    response = client.messages.create(
        model=_GENERATOR_MODEL,
        max_tokens=_GENERATOR_MAX_TOKENS,
        temperature=_GENERATOR_TEMPERATURE,
        system="You are a senior recruiter and engineering hiring manager. Output only valid YAML.",
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text  # type: ignore[attr-defined]
    # Strip optional fences.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    blob = yaml.safe_load(raw)
    blob.setdefault("archetype_id", archetype_id)

    rubric = ArchetypeRubric.model_validate(blob)
    if rubric.archetype_id != archetype_id:
        raise ValueError(
            f"archetype_id mismatch: requested {archetype_id!r}, "
            f"Sonnet emitted {rubric.archetype_id!r}"
        )

    out.write_text(yaml.safe_dump(blob, sort_keys=False), encoding="utf-8")
    logger.info("Wrote rubric to %s", out)
    return rubric
