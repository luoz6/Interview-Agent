from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from app.services.interview_quality_dataset import InterviewQualityDataset
from app.services.interview_quality_provider_authorization import (
    ProviderAuthorizationManifest,
    ProviderRunRequest,
    validate_provider_run,
)


PRICING_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing"
MODELS_ENDPOINT = "https://api.deepseek.com/models"


class ProviderPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_hit_input_per_million: float = Field(ge=0)
    cache_miss_input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    currency: Literal["USD"] = "USD"


class DeepSeekDiscoverySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: str
    models_endpoint: Literal["https://api.deepseek.com/models"] = MODELS_ENDPOINT
    models_endpoint_ok: bool
    model_request_attempts: int = Field(default=1, ge=0)
    model_ids: list[str]
    pricing_source_url: Literal[
        "https://api-docs.deepseek.com/quick_start/pricing"
    ] = PRICING_SOURCE_URL
    pricing_page_ok: bool
    pricing_request_attempts: int = Field(default=1, ge=0)
    prices: dict[str, ProviderPrice]
    error_code: Literal["credential", "network", "invalid_response"] | None = None


class FollowupProviderPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: Literal["T36"] = "T36"
    authorization_id: str
    provider_name: str
    authorized_model: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_preflight_passed: bool
    dataset_manifest_match: bool
    gate_config_manifest_match: bool
    authorization_manifest_match: bool
    credential_present: bool
    model_available: bool
    pricing_available: bool
    evidence_persistence_available: bool
    environment_model: str | None = None
    environment_model_ignored: bool = False
    discovery: DeepSeekDiscoverySnapshot
    hard_stop_conditions: list[str]

    @property
    def allowed(self) -> bool:
        return not self.hard_stop_conditions


def discover_deepseek_provider(
    *,
    api_key: str | None,
    timeout_seconds: float = 30,
) -> DeepSeekDiscoverySnapshot:
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    model_ids: list[str] = []
    prices: dict[str, ProviderPrice] = {}
    models_ok = pricing_ok = False
    model_attempts = pricing_attempts = 0
    error_code: Literal["credential", "network", "invalid_response"] | None = None

    if not api_key:
        error_code = "credential"
    else:
        for _ in range(3):
            model_attempts += 1
            try:
                request = Request(
                    MODELS_ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"},
                    method="GET",
                )
                with urlopen(request, timeout=timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                model_ids = sorted(
                    str(item["id"])
                    for item in payload.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                )
                models_ok = True
                error_code = None
                break
            except HTTPError as exc:
                error_code = "credential" if exc.code in {401, 403} else "network"
                if error_code == "credential":
                    break
            except (URLError, TimeoutError):
                error_code = "network"
            except (UnicodeError, ValueError, TypeError, KeyError):
                error_code = "invalid_response"

    for _ in range(3):
        pricing_attempts += 1
        try:
            request = Request(PRICING_SOURCE_URL, method="GET")
            with urlopen(request, timeout=timeout_seconds) as response:
                content = response.read().decode("utf-8", errors="replace")
            prices = parse_deepseek_pricing_table(content)
            pricing_ok = True
            break
        except (HTTPError, URLError, TimeoutError, UnicodeError):
            if error_code is None:
                error_code = "network"

    return DeepSeekDiscoverySnapshot(
        observed_at=observed_at,
        models_endpoint_ok=models_ok,
        model_request_attempts=model_attempts,
        model_ids=model_ids,
        pricing_page_ok=pricing_ok,
        pricing_request_attempts=pricing_attempts,
        prices=prices,
        error_code=error_code,
    )


def parse_deepseek_pricing_table(content: str) -> dict[str, ProviderPrice]:
    """Extract the official HTML table without depending on page layout tools."""

    model_match = re.search(
        r"<tr><td[^>]*>MODEL</td>(?P<cells>.*?)</tr>",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not model_match:
        return {}
    models = _table_cells(model_match.group("cells"))
    if not models:
        return {}

    rows: dict[str, list[float]] = {}
    labels = {
        "CACHE HIT": "cache_hit",
        "CACHE MISS": "cache_miss",
        "OUTPUT TOKENS": "output",
    }
    for row_match in re.finditer(
        r"<tr>(?P<row>.*?)</tr>",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        cells = _table_cells(row_match.group("row"))
        if not cells:
            continue
        label = " ".join(cells).upper()
        key = next((value for marker, value in labels.items() if marker in label), None)
        if key is None:
            continue
        numeric = []
        for cell in cells:
            match = re.fullmatch(r"\$([0-9]+(?:\.[0-9]+)?)", cell.strip())
            if match:
                numeric.append(float(match.group(1)))
        if len(numeric) == len(models):
            rows[key] = numeric
    if set(rows) != {"cache_hit", "cache_miss", "output"}:
        return {}
    return {
        model: ProviderPrice(
            cache_hit_input_per_million=rows["cache_hit"][index],
            cache_miss_input_per_million=rows["cache_miss"][index],
            output_per_million=rows["output"][index],
        )
        for index, model in enumerate(models)
    }


def evaluate_followup_provider_preflight(
    *,
    manifest: ProviderAuthorizationManifest,
    dataset: InterviewQualityDataset,
    dataset_path: Path,
    gate_config_path: Path,
    authorization_path: Path,
    dataset_file_manifest_path: Path,
    execution_manifest_path: Path,
    discovery: DeepSeekDiscoverySnapshot,
    credential_present: bool,
    evidence_persistence_available: bool,
    environment_model: str | None,
) -> FollowupProviderPreflightResult:
    dataset_sha256 = _sha256_file(dataset_path)
    gate_sha256 = _sha256_file(gate_config_path)
    authorization_sha256 = _sha256_file(authorization_path)
    dataset_manifest = json.loads(
        dataset_file_manifest_path.read_text(encoding="utf-8")
    )
    execution_manifest = json.loads(
        execution_manifest_path.read_text(encoding="utf-8")
    )
    dataset_match = (
        dataset_manifest.get("files", {}).get(dataset_path.name) == dataset_sha256
    )
    frozen_gate = execution_manifest.get("gate_0", {})
    gate_match = frozen_gate.get("gate_config_sha256") == gate_sha256
    authorization_match = (
        frozen_gate.get("provider_authorization_sha256") == authorization_sha256
    )
    redaction_passed = _redaction_preflight(dataset)
    categories = {
        {
            "synthetic": "synthetic_candidate_answers",
            "public": "public_technical_material",
            "redacted": "manually_or_deterministically_redacted_cases",
        }[case.source_boundary.classification]
        for case in dataset.cases
    }
    request = ProviderRunRequest(
        task="T36",
        provider_name=manifest.provider.name,
        base_url=manifest.provider.base_url,
        model_id=manifest.provider.model_id,
        data_categories=categories,
        redaction_preflight_passed=redaction_passed,
        usage_metering_available=True,
        evidence_persistence_available=evidence_persistence_available,
    )
    stops = list(validate_provider_run(manifest, request))
    if not credential_present or discovery.error_code == "credential":
        stops.append("CREDENTIAL_UNAVAILABLE")
    if not dataset_match or not gate_match or not authorization_match:
        stops.append("GATE_CONFIG_OR_DATASET_DRIFT")
    model_available = (
        discovery.models_endpoint_ok
        and manifest.provider.model_id in discovery.model_ids
    )
    if discovery.models_endpoint_ok and not model_available:
        stops.append("MODEL_VERSION_DRIFT")
    pricing_available = (
        discovery.pricing_page_ok
        and manifest.provider.model_id in discovery.prices
    )
    if model_available and not pricing_available:
        stops.append("USAGE_METERING_UNAVAILABLE")
    if discovery.error_code in {"network", "invalid_response"} and (
        discovery.model_request_attempts >= 3
        or discovery.pricing_request_attempts >= 3
    ):
        stops.append("REPEATED_PROVIDER_FAILURE")
    stops = list(dict.fromkeys(stops))
    return FollowupProviderPreflightResult(
        authorization_id=manifest.authorization_id,
        provider_name=manifest.provider.name,
        authorized_model=manifest.provider.model_id,
        dataset_sha256=dataset_sha256,
        gate_config_sha256=gate_sha256,
        authorization_sha256=authorization_sha256,
        redaction_preflight_passed=redaction_passed,
        dataset_manifest_match=dataset_match,
        gate_config_manifest_match=gate_match,
        authorization_manifest_match=authorization_match,
        credential_present=credential_present,
        model_available=model_available,
        pricing_available=pricing_available,
        evidence_persistence_available=evidence_persistence_available,
        environment_model=environment_model,
        environment_model_ignored=bool(
            environment_model and environment_model != manifest.provider.model_id
        ),
        discovery=discovery,
        hard_stop_conditions=stops,
    )


def estimate_provider_cost(
    *,
    price: ProviderPrice,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    if min(input_tokens, output_tokens, cached_input_tokens) < 0:
        raise ValueError("token counts must be non-negative")
    cached = min(input_tokens, cached_input_tokens)
    uncached = input_tokens - cached
    return (
        cached / 1_000_000 * price.cache_hit_input_per_million
        + uncached / 1_000_000 * price.cache_miss_input_per_million
        + output_tokens / 1_000_000 * price.output_per_million
    )


def _redaction_preflight(dataset: InterviewQualityDataset) -> bool:
    return all(
        case.provider_allowed
        and not case.source_boundary.contains_real_candidate_data
        and not case.source_boundary.contains_employer_confidential_data
        and not case.source_boundary.contains_principal_memory
        for case in dataset.cases
    )


def _table_cells(value: str) -> list[str]:
    cells = re.findall(
        r"<td[^>]*>(.*?)</td>",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
        for cell in cells
    ]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
