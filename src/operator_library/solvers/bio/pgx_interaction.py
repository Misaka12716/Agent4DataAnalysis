'''Drug-Gene interaction (PGx) testing - drug x SNP scan on outcome.

Fits regression models for each SNP to test the interaction term
drug_col * snp_encoded. Supports binary (logistic) and continuous
(linear) outcomes with additive/dominant/recessive SNP coding.

Reference:
- Hayes AF (2018) Introduction to Mediation, Moderation, and Conditional Process Analysis.
- Aiken LS, West SG (1991) Multiple Regression: Testing and Interpreting Interactions.
'''
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name='pgx_interaction',
    capability='F12_association_comorbidity_pattern',
    description=(
        'Drug x SNP interaction scanning. For each SNP column, fit a '
        'regression with drug*snp interaction term and report '
        'interaction coefficient, p-value, and OR (if binary outcome). '
        'Output: pgx_interaction_results.csv.'
    ),
    roles={
        'drug_col': RoleSpec(
            Role.NUMERIC,
            'Drug / treatment column (binary 0/1 or multi-level)',
        ),
        'snp_cols': RoleSpec(
            Role.NUMERIC_LIST,
            'SNP columns to scan (should be encoded as 0/1/2 additive or as specified by snp_coding param)',
        ),
        'outcome_col': RoleSpec(
            Role.NUMERIC if True else Role.BINARY_TARGET,
            'Outcome column (binary 0/1 or continuous for linear)',
            optional=False,
        ),
        'covariates': RoleSpec(
            Role.NUMERIC_LIST,
            'Covariate columns to adjust for',
            optional=True,
        ),
    },
    static_params={
        'outcome_type': 'binary',
        'snp_coding': 'additive',
        'multiple_test': 'fdr_bh',
        'alpha': 0.05,
    },
    output_files={'pgx_results_csv': 'pgx_interaction_results.csv'},
    output_kind={'pgx_results_csv': 's'},
)

ROLE_OVERRIDE = {
    'outcome_col': RoleSpec(
        Role.NUMERIC,
        'Outcome column (binary 0/1 or continuous for linear)',
    ),
}
# Override on-the-fly since role can be numeric or binary
CONTRACT = SolverContract(
    **{**CONTRACT.__dict__,
       'roles': {**CONTRACT.roles, **ROLE_OVERRIDE}}
) if False else CONTRACT


class PGxInteractionSolver:
    contract = CONTRACT

    def __init__(self, outcome_type: str = 'binary',
                 snp_coding: str = 'additive',
                 multiple_test: str = 'fdr_bh',
                 alpha: float = 0.05):
        self.outcome_type = outcome_type
        self.snp_coding = snp_coding
        self.multiple_test = multiple_test
        self.alpha = alpha

    @staticmethod
    def _encode_snp(series: pd.Series, coding: str = 'additive') -> np.ndarray:
        # First try numeric encoding directly
        if coding == 'additive':
            # Try direct numeric conversion (handles 0, 1, 2 and 0.0, 1.0, 2.0)
            numeric_vals = pd.to_numeric(series, errors='coerce')
            if not numeric_vals.isna().all():
                vals_numeric = numeric_vals.fillna(1).values.astype(float)
                if set(np.unique(vals_numeric)).issubset({0.0, 1.0, 2.0}):
                    return vals_numeric
            # Fallback to string-based genotype counting
            vals = series.astype(str).str.upper().str.strip()
            unique_vals = sorted(set(vals.dropna()))
            if set(unique_vals).issubset({'0', '1', '2'}):
                return pd.to_numeric(vals, errors='coerce').fillna(1).values
            mapping = {}
            for v in unique_vals:
                cnt = v.count('A') if 'A' in v else v.count('G') if 'G' in v else v.count('C') if 'C' in v else v.count('T') if 'T' in v else 0
                mapping[v] = cnt
            result = vals.map(mapping)
            if result.isna().any():
                result = pd.to_numeric(vals, errors='coerce')
            return result.fillna(1).values.astype(float)
        elif coding == 'dominant':
            vals2 = vals.copy()
            most_common = vals2.value_counts().index[0] if len(vals2) > 0 else ''
            return (vals2 != most_common).astype(float).values
        elif coding == 'recessive':
            vals2 = vals.copy()
            least_common = vals2.value_counts().index[-1] if len(vals2) > 1 else ''
            return (vals2 == least_common).astype(float).values
        else:
            raise ValueError(f'Unknown snp_coding: {coding}')

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        drug_col = mapping.get('drug_col')
        snp_cols = mapping.get('snp_cols') or []
        outcome_col = mapping.get('outcome_col')
        covariates = mapping.get('covariates') or []

        if not drug_col or not outcome_col:
            raise ValueError('drug_col and outcome_col are required')
        if not snp_cols:
            raise ValueError('at least one SNP column required')

        if isinstance(snp_cols, str):
            snp_cols = [snp_cols]
        if isinstance(covariates, str):
            covariates = [covariates]

        results = []
        for snp in snp_cols:
            sub = df[[drug_col, snp, outcome_col] + list(covariates)].dropna().copy()
            n = len(sub)
            if n < 30:
                results.append({
                    'snp': snp, 'n_obs': n, 'skip_reason': 'n<30',
                    'interaction_pvalue': np.nan,
                })
                continue

            x_snp = self._encode_snp(sub[snp], coding=self.snp_coding)
            sub['_snp_encoded'] = x_snp
            sub['_interaction'] = sub[drug_col].values * x_snp

            exog_cols = [drug_col, '_snp_encoded', '_interaction'] + list(covariates)
            exog = sub[exog_cols].copy()
            exog = sm.add_constant(exog.astype(float))
            endog = sub[outcome_col].astype(float)

            try:
                if self.outcome_type == 'binary':
                    model = sm.Logit(endog, exog).fit(disp=0)
                else:
                    model = sm.OLS(endog, exog).fit()
            except Exception:
                results.append({
                    'snp': snp, 'n_obs': n, 'skip_reason': 'model_fit_failed',
                    'interaction_pvalue': np.nan,
                })
                continue

            int_idx = list(exog.columns).index('_interaction')
            coef = float(model.params.iloc[int_idx])
            se = float(model.bse.iloc[int_idx])
            z = coef / se if se > 0 else 0.0

            if self.outcome_type == 'binary':
                pval = float(2 * (1 - model.model._cdf_abs(z))) if hasattr(model.model, '_cdf_abs') else float(model.pvalues.iloc[int_idx])
                try:
                    or_val = float(np.exp(coef))
                except Exception:
                    or_val = None
                ci_low = float(np.exp(coef - 1.96 * se)) if or_val else None
                ci_high = float(np.exp(coef + 1.96 * se)) if or_val else None
            else:
                import scipy.stats as sps
                pval = float(2 * (1 - sps.norm.cdf(abs(z))))
                or_val = None
                ci_low = float(coef - 1.96 * se)
                ci_high = float(coef + 1.96 * se)

            results.append({
                'snp': snp,
                'interaction_coef': coef,
                'interaction_se': se,
                'interaction_pvalue': pval,
                'interaction_or': or_val,
                'interaction_ci_low': ci_low,
                'interaction_ci_high': ci_high,
                'n_obs': n,
            })

        if not results:
            out_df = pd.DataFrame()
        else:
            out_df = pd.DataFrame(results)
            pvals = out_df['interaction_pvalue'].values
            valid = ~np.isnan(pvals)
            if valid.any():
                _, adj_p, _, _ = multipletests(pvals[valid], alpha=self.alpha, method=self.multiple_test)
                out_df['interaction_pvalue_adj'] = np.nan
                out_df.loc[valid, 'interaction_pvalue_adj'] = adj_p
                out_df['significant'] = out_df['interaction_pvalue_adj'] < self.alpha
            else:
                out_df['interaction_pvalue_adj'] = np.nan
                out_df['significant'] = False

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files['pgx_results_csv']
        out_df.to_csv(path, index=False)

        n_sig = int(out_df['significant'].sum()) if 'significant' in out_df.columns else 0
        return {
            'pgx_results_csv': str(path),
            'n_snps_tested': len(results),
            'n_significant': n_sig,
            'correction_method': self.multiple_test,
            'alpha': self.alpha,
            'outcome_type': self.outcome_type,
            'snp_coding': self.snp_coding,
        }


def get_solver(outcome_type: str = 'binary', snp_coding: str = 'additive',
               multiple_test: str = 'fdr_bh', alpha: float = 0.05):
    return PGxInteractionSolver(
        outcome_type=outcome_type, snp_coding=snp_coding,
        multiple_test=multiple_test, alpha=alpha)


def selftest():
    '''Generate synthetic drug x SNP interaction data and verify detection.'''
    import tempfile
    rng = np.random.default_rng(42)
    n = 200
    drug = rng.binomial(1, 0.5, n).astype(float)
    snp1 = rng.integers(0, 3, n).astype(float)  # additive
    snp2 = rng.integers(0, 3, n).astype(float)

    # true interaction: drug * snp1
    log_odds = -1.0 + 0.5 * drug + 0.1 * snp1 + 0.8 * drug * snp1 + 0.05 * snp2
    prob = 1.0 / (1.0 + np.exp(-log_odds))
    outcome = (rng.random(n) < prob).astype(float)

    df = pd.DataFrame({'drug': drug, 'snp_a': snp1, 'snp_b': snp2, 'outcome': outcome})
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        s = get_solver(outcome_type='binary')
        out = s.run(df, ColumnMapping({
            'drug_col': 'drug', 'snp_cols': ['snp_a', 'snp_b'],
            'outcome_col': 'outcome',
        }), tmp)
        res_df = pd.read_csv(out['pgx_results_csv'])
        snp_a_row = res_df[res_df['snp'] == 'snp_a']
        if snp_a_row.empty or snp_a_row['interaction_pvalue'].values[0] > 0.05:
            diffs.append('snp_a interaction not detected (p should be < 0.05)')
        if res_df[res_df['snp'] == 'snp_b'].empty:
            diffs.append('snp_b missing from results')
    return {
        'ok': len(diffs) == 0,
        'summary': 'PGx interaction scan detects true interaction' if not diffs else f'{len(diffs)} mismatch(es)',
        'details': {'diffs': diffs, 'tested': ['pgx_interaction']},
    }
