# clinical_mapping/import_contracts.py — 临床导入表列映射契约

from __future__ import annotations

from operator_library.contract import Role, RoleSpec, SolverContract

PATIENT_IMPORT_CONTRACT = SolverContract(
    name="clinical_patient_import",
    capability="clinical_patient",
    description=(
        "Map user-uploaded psychiatric patient table columns to the "
        "canonical patient schema for cohort screening and risk analysis."
    ),
    roles={
        "patient_id": RoleSpec(
            Role.ID,
            "Unique patient identifier (e.g. patient_id, 患者编号, subject_id)",
        ),
        "age": RoleSpec(Role.NUMERIC, "Patient age in years", optional=True),
        "gender": RoleSpec(
            Role.CATEGORICAL,
            "Sex/gender (female/male or F/M)",
            optional=True,
        ),
        "diagnosis": RoleSpec(
            Role.CATEGORICAL,
            "Primary psychiatric diagnosis (depression/anxiety/schizophrenia/...)",
            optional=True,
        ),
        "admission_date": RoleSpec(Role.DATETIME, "Admission date", optional=True),
        "discharge_date": RoleSpec(Role.DATETIME, "Discharge date", optional=True),
        "HAMD_total": RoleSpec(Role.NUMERIC, "HAMD-17 total score", optional=True),
        "HAMA_total": RoleSpec(Role.NUMERIC, "HAMA total score", optional=True),
        "PHQ9_total": RoleSpec(Role.NUMERIC, "PHQ-9 total score", optional=True),
        "disease_duration_years": RoleSpec(
            Role.NUMERIC, "Disease duration in years", optional=True
        ),
        "medication": RoleSpec(Role.TEXT, "Current medication text", optional=True),
        "outcome": RoleSpec(Role.CATEGORICAL, "Treatment outcome label", optional=True),
        "relapse": RoleSpec(
            Role.BINARY_TARGET,
            "Relapse indicator 0/1 or yes/no",
            optional=True,
        ),
    },
)

FOLLOWUP_IMPORT_CONTRACT = SolverContract(
    name="clinical_followup_import",
    capability="clinical_followup",
    description="Map follow-up visit rows to canonical follow-up schema.",
    roles={
        "patient_id": RoleSpec(Role.ID, "Patient identifier matching patient table"),
        "visit_date": RoleSpec(Role.DATETIME, "Visit/follow-up date"),
        "visit_type": RoleSpec(
            Role.CATEGORICAL,
            "Visit type (baseline/week4/week8/...)",
            optional=True,
        ),
        "HAMD_total": RoleSpec(Role.NUMERIC, "HAMD total at visit", optional=True),
        "HAMA_total": RoleSpec(Role.NUMERIC, "HAMA total at visit", optional=True),
        "PHQ9_total": RoleSpec(Role.NUMERIC, "PHQ-9 total at visit", optional=True),
        "medication": RoleSpec(Role.TEXT, "Medication at visit", optional=True),
        "medication_dose_mg": RoleSpec(
            Role.NUMERIC, "Medication dose in mg", optional=True
        ),
        "notes": RoleSpec(Role.TEXT, "Clinical notes", optional=True),
    },
)

REFERENCE_IMPORT_CONTRACT = SolverContract(
    name="clinical_reference_import",
    capability="clinical_reference",
    description="Map reference-range definition rows to canonical reference schema.",
    roles={
        "indicator": RoleSpec(
            Role.CATEGORICAL,
            "Lab/scale indicator name (HAMD_total, HAMA_total, PHQ9_total, ...)",
        ),
        "lower_bound": RoleSpec(Role.NUMERIC, "Lower bound of reference interval"),
        "upper_bound": RoleSpec(Role.NUMERIC, "Upper bound of reference interval"),
        "gender": RoleSpec(Role.CATEGORICAL, "Applicable gender filter", optional=True),
        "diagnosis": RoleSpec(
            Role.CATEGORICAL, "Applicable diagnosis filter", optional=True
        ),
        "age_range_lower": RoleSpec(
            Role.NUMERIC, "Minimum applicable age", optional=True
        ),
        "age_range_upper": RoleSpec(
            Role.NUMERIC, "Maximum applicable age", optional=True
        ),
        "unit": RoleSpec(Role.TEXT, "Measurement unit", optional=True),
        "source": RoleSpec(Role.TEXT, "Reference source/guideline", optional=True),
    },
)

IMPORT_CONTRACTS = {
    "patient": PATIENT_IMPORT_CONTRACT,
    "followup": FOLLOWUP_IMPORT_CONTRACT,
    "reference": REFERENCE_IMPORT_CONTRACT,
}
