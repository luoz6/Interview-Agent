import app.runtime.interview_prep as canonical_prep
import app.runtime.prepared_plan as canonical_prepared_plan


def test_interview_prep_exports_canonical_revision_binding_functions():
    assert (
        canonical_prep.bind_prepared_plan_revision
        is canonical_prepared_plan.bind_prepared_plan_revision
    )
    assert (
        canonical_prep.prepared_plan_revision
        is canonical_prepared_plan.prepared_plan_revision
    )
