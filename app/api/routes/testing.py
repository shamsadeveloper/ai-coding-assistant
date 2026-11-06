"""Test routes: detection, coverage gaps, generation with review/approval, and test runs."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_testing_service
from app.schemas.testing import (
    ApproveTestRequest,
    CoverageGapOut,
    GeneratedTestOut,
    GenerateTestRequest,
    RunTestsRequest,
    TestFrameworkOut,
    TestRunOut,
)
from app.services.testing.service import TestingService

router = APIRouter(prefix="/api/v1/tests", tags=["tests"])

ServiceDep = Annotated[TestingService, Depends(get_testing_service)]


@router.get("/frameworks", response_model=list[TestFrameworkOut])
def frameworks(repository_id: int, service: ServiceDep) -> list[TestFrameworkOut]:
    return [TestFrameworkOut(**asdict(f)) for f in service.frameworks(repository_id)]


@router.get("/gaps", response_model=list[CoverageGapOut])
def coverage_gaps(
    repository_id: int, service: ServiceDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[CoverageGapOut]:
    """Source files whose public functions/classes are not mentioned in any test (heuristic)."""
    return [CoverageGapOut(**asdict(g)) for g in service.gaps(repository_id, limit)]


@router.post("/generate", response_model=GeneratedTestOut)
async def generate(request: GenerateTestRequest, service: ServiceDep) -> GeneratedTestOut:
    """Generate a test into the review area. The repository is not modified."""
    record = await service.generate(request.repository_id, request.path, request.symbol, request.framework)
    return GeneratedTestOut.model_validate(record)


@router.get("/generated", response_model=list[GeneratedTestOut])
def list_generated(repository_id: int, service: ServiceDep) -> list[GeneratedTestOut]:
    return [GeneratedTestOut.model_validate(r) for r in service.list_generated(repository_id)]


@router.get("/generated/{generated_id}", response_model=GeneratedTestOut)
def get_generated(generated_id: int, service: ServiceDep) -> GeneratedTestOut:
    return GeneratedTestOut.model_validate(service.get_generated(generated_id))


@router.post("/generated/{generated_id}/approve", response_model=GeneratedTestOut)
def approve(generated_id: int, request: ApproveTestRequest, service: ServiceDep) -> GeneratedTestOut:
    """Developer approval: write the test into the repository (backing up any file it replaces)."""
    return GeneratedTestOut.model_validate(service.approve(generated_id, request.overwrite))


@router.post("/generated/{generated_id}/reject", response_model=GeneratedTestOut)
def reject(generated_id: int, service: ServiceDep) -> GeneratedTestOut:
    return GeneratedTestOut.model_validate(service.reject(generated_id))


@router.post("/run", response_model=TestRunOut)
def run_tests(request: RunTestsRequest, service: ServiceDep) -> TestRunOut:
    """Run tests with the configured command for the framework (no shell, timeout, minimal environment)."""
    return TestRunOut.model_validate(service.run(request.repository_id, request.framework, request.path))


@router.get("/runs/{run_id}", response_model=TestRunOut)
def get_run(run_id: int, service: ServiceDep) -> TestRunOut:
    return TestRunOut.model_validate(service.get_run(run_id))


@router.post("/runs/{run_id}/analyze", response_model=TestRunOut)
async def analyze_run(run_id: int, service: ServiceDep) -> TestRunOut:
    """Ask the model to explain failures in a stored test run."""
    return TestRunOut.model_validate(await service.analyze_run(run_id))
