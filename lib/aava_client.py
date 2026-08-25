"""
Client for Member C's extraction, running as an AAVA agent workflow.

Two-call async pattern, based on the captured network traffic:
  1. POST {base}/workflows/workflow-executions
     -> kicks off the job, returns {"data": {"jobId": ..., "workflowExecutionId": ...}}
  2. GET  {base}/workflows/workflow-executions/{execution_id}/result
     -> poll until status == "SUCCESS" (or a failure status), then the
        agent's raw text output is nested at
        data.result.workflow_execution... actually the field that matters
        is data.result.response -> (parsed JSON) -> tasksOutputs[0].raw
        This is the LLM's raw text reply, which per Member C's prompt
        should be ONLY a JSON array — but defensively, always strip
        markdown fences and try/except the json.loads(), same guidance
        Member C's own doc gives for parsing their LLM's output.

IMPORTANT — auth:
  aava_api_token must be a stable service-account token, not a copied
  browser session token. Browser tokens are short-lived JWTs (~1hr) and
  will silently start failing. Get a proper server-to-server credential
  from whoever administers your AAVA org before deploying this anywhere
  beyond local testing.
"""
import json
import re
import time
from typing import Any

import requests

from lib.config import settings


class AavaExtractionError(Exception):
    pass


def _auth_headers() -> dict[str, str]:
    if not settings.aava_api_token:
        raise AavaExtractionError(
            "AAVA_API_TOKEN is not set. Add a stable service-account token "
            "to .env — never hardcode it in source."
        )
    return {
        "Authorization": f"Bearer {settings.aava_api_token}",
        "Accept": "application/json",
    }


def _submit_job(psa_path: str, amendment_path: str | None, user_email: str) -> str:
    """
    POST the extraction job. Returns the workflow execution id to poll.

    CONFIRMED shape (captured directly from AAVA's own Playground UI
    submitting a real file, not guessed): AAVA does NOT want separate
    multipart fields per PDF. It wants exactly:
        pipelineId  -> the workflow id, as a plain string
        userInputs  -> JSON string, template var keys mapped to "" (the
                       actual file content goes in the zip, not here)
        priority    -> "1"
        files       -> ONE multipart file field, containing a ZIP
                       archive (named "all-files.zip") with the PDF(s)
                       inside it at their real filenames
                       (e.g. psa_exhibit.pdf, amendment.pdf)
    """
    import io
    import zipfile

    url = f"{settings.aava_api_base}/workflows/workflow-executions"

    user_inputs = {"{{psa_exhibit_file}}": "", "{{amendment_file}}": ""}

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(psa_path, arcname="psa_exhibit.pdf")
        if amendment_path:
            zf.write(amendment_path, arcname="amendment.pdf")
    zip_buffer.seek(0)

    data = {
        "pipelineId": str(settings.aava_workflow_id),
        "userInputs": json.dumps(user_inputs),
        "priority": "1",
    }
    files = {
        "files": ("all-files.zip", zip_buffer, "application/zip"),
    }

    resp = requests.post(url, headers=_auth_headers(), data=data, files=files, timeout=30)
    resp.raise_for_status()
    body = resp.json()

    if body.get("status") != "SUCCESS":
        raise AavaExtractionError(f"AAVA job submission failed: {body}")

    execution_id = body["data"].get("workflowExecutionId")
    if not execution_id:
        raise AavaExtractionError(f"No workflowExecutionId in response: {body}")
    return execution_id


def _poll_for_result(execution_id: str) -> dict[str, Any]:
    """Poll GET .../result until status is SUCCESS or timeout is hit."""
    url = f"{settings.aava_api_base}/workflows/workflow-executions/{execution_id}/result"
    deadline = time.monotonic() + settings.aava_poll_timeout_sec

    while time.monotonic() < deadline:
        resp = requests.get(url, headers=_auth_headers(), timeout=30)
        resp.raise_for_status()
        body = resp.json()

        status = body.get("data", {}).get("status") or body.get("status")
        if status == "SUCCESS":
            return body
        if status in ("FAILED", "ERROR"):
            raise AavaExtractionError(f"AAVA job {execution_id} failed: {body}")

        time.sleep(settings.aava_poll_interval_sec)

    raise AavaExtractionError(f"AAVA job {execution_id} timed out after {settings.aava_poll_timeout_sec}s")


def _extract_raw_agent_output(result_body: dict[str, Any]) -> str:
    """
    Pull the agent's raw text reply out of the (deeply nested) result
    payload. Confirmed shape from an actual captured response:
        data.result.response  -> JSON string -> has a top-level "output"
        string (the agent's raw reply), and the same text duplicated at
        tasksOutputs[0]["raw"]. Try both, in that order.
    """
    response_str = result_body["data"]["result"]["response"]
    response_obj = json.loads(response_str)

    output = response_obj.get("output")
    if output:
        return output

    tasks_outputs = response_obj.get("tasksOutputs") or []
    if tasks_outputs and tasks_outputs[0].get("raw"):
        return tasks_outputs[0]["raw"]

    _dump_debug(result_body, response_obj, reason="no_output_or_tasksOutputs_raw")
    raise AavaExtractionError(
        "Could not find agent output under 'output' or 'tasksOutputs[0].raw'"
    )


def _dump_debug(result_body: dict[str, Any], response_obj: dict[str, Any], reason: str) -> None:
    """
    Write the full response shape to disk so we can see exactly what AAVA
    actually returned, instead of guessing at the key path again. Check
    ./data/aava_debug/ after a failed run.
    """
    import os
    from datetime import datetime, timezone

    os.makedirs("./data/aava_debug", exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = f"./data/aava_debug/{ts}_{reason}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"reason": reason, "full_result_body": result_body, "parsed_response_obj": response_obj},
            f,
            indent=2,
            default=str,
        )
    import logging
    logging.getLogger(__name__).error("AAVA response shape unexpected (%s) — dumped to %s", reason, path)


def _parse_json_array(raw_text: Any) -> list[dict[str, Any]]:
    """
    Normalize whatever AAVA gave us into a list of contract-term dicts.

    AAVA can apparently hand back the extraction result two different
    ways: as a JSON *string* the agent literally typed (needs
    json.loads, markdown-fence stripping, etc — the original assumption),
    OR already parsed into a real Python list/dict on AAVA's own side
    (seen in practice: raw_text arrived as a dict, not a string).
    Handle both rather than assuming one.
    """
    if isinstance(raw_text, list):
        return raw_text

    if isinstance(raw_text, dict):
        _dump_debug({}, {"raw_text_dict": raw_text}, reason="raw_text_was_dict_not_str")
        # Common wrapper shapes — try the obvious ones before giving up.
        for key in ("contract_terms", "data", "result", "results", "rows", "items"):
            if isinstance(raw_text.get(key), list):
                return raw_text[key]
        # If the dict itself looks like a single contract-term row
        # (has the schema's own keys), treat it as a one-row result.
        if "tin" in raw_text or "cpt_code" in raw_text:
            return [raw_text]
        return []

    if not isinstance(raw_text, str):
        return []

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Agent replied with prose instead of a JSON array (e.g. "no files
        # attached" like the test run in the capture) — treat as
        # extraction failure, same as an empty list.
        return []

    if isinstance(parsed, dict):
        return _parse_json_array(parsed)  # reuse the dict-handling above
    if not isinstance(parsed, list):
        return []
    return parsed


def extract_via_aava(
    upload_id: str, psa_path: str, amendment_path: str | None, user_email: str
) -> list[dict[str, Any]]:
    execution_id = _submit_job(psa_path, amendment_path, user_email)
    result_body = _poll_for_result(execution_id)
    raw_text = _extract_raw_agent_output(result_body)
    parsed = _parse_json_array(raw_text)

    if not parsed:
        import logging
        logging.getLogger(__name__).error(
            "AAVA returned no contract terms for upload_id=%s. Raw agent reply "
            "(first 500 chars): %s", upload_id, raw_text[:500]
        )
    return parsed
