'''Mediation Analysis - Baron & Kenny four-step with Bootstrap.

Estimates whether X influences Y through mediator M.
Fits three OLS regressions and uses bootstrap to estimate
the confidence interval of the indirect effect (a*b).

References:
- Baron R, Kenny D (1986) JPSP 51:1173
- Preacher KJ, Hayes AF (2008) Behav Res Methods 40:879
'''
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ...contract import ColumnMapping, Role, RoleSpec, SolverContract

CONTRACT = SolverContract(
    name='mediation_analysis',
    capability='F12_association_comorbidity_pattern',
    description=(
        'Mediation analysis using Baron-Kenny 4-step method + '
        'Bootstrap estimation of indirect effect CI. Estimates '
        'total effect (c), direct effect (c prime), and indirect '
        'effect (a*b), the proportion mediated, and bootstrap p-value. '
        'Output: mediation_results.csv.'
    ),
    roles={
        'x_col': RoleSpec(
            Role.NUMERIC,
            'Independent variable column (e.g. drug / treatment)',
        ),
        'm_col': RoleSpec(
            Role.NUMERIC,
            'Mediator variable column (e.g. adherence)',
        ),
        'y_col': RoleSpec(
            Role.NUMERIC,
            'Outcome variable column (e.g. relapse)',
        ),
        'covariates': RoleSpec(
            Role.NUMERIC_LIST,
            'Covariate columns to adjust for (e.g. age, gender)',
            optional=True,
        ),
    },
    static_params={'n_bootstrap': 1000, 'ci_level': 0.95, 'random_state': 42},
    output_files={'mediation_results_csv': 'mediation_results.csv'},
    output_kind={'mediation_results_csv': 's'},
)


class MediationAnalysisSolver:
    contract = CONTRACT

    def __init__(self, n_bootstrap: int = 1000, ci_level: float = 0.95,
                 random_state: int = 42):
        self.n_bootstrap = n_bootstrap
        self.ci_level = ci_level
        self.random_state = random_state

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        x_col = mapping.get('x_col')
        m_col = mapping.get('m_col')
        y_col = mapping.get('y_col')
        covariates = mapping.get('covariates') or []

        if not all([x_col, m_col, y_col]):
            raise ValueError('x_col, m_col, y_col are all required')

        sub = df[[x_col, m_col, y_col] + list(covariates)].dropna().copy()
        n = len(sub)
        if n < 30:
            raise ValueError(f'Sample size {n} too small; need n>=30')

        X = sub[x_col].values
        M = sub[m_col].values
        Y = sub[y_col].values
        Z = sub[list(covariates)].values if covariates else np.zeros((n, 0))

        def _fit_ols(endog, exog):
            X_design = sm.add_constant(exog)
            return sm.OLS(endog, X_design).fit()

        # Step 1: total effect X -> Y (c)
        exog_xy = np.column_stack([X.reshape(-1, 1), Z]) if Z.shape[1] else X.reshape(-1, 1)
        c_model = _fit_ols(Y, exog_xy)
        total_effect = float(c_model.params[1])

        # Step 2: X -> M (path a)
        exog_xm = np.column_stack([X.reshape(-1, 1), Z]) if Z.shape[1] else X.reshape(-1, 1)
        a_model = _fit_ols(M, exog_xm)
        path_a = float(a_model.params[1])

        # Step 3: X + M -> Y (path b, direct effect c')
        exog_xmy = np.column_stack([X.reshape(-1, 1), M.reshape(-1, 1), Z]) if Z.shape[1] else np.column_stack([X.reshape(-1, 1), M.reshape(-1, 1)])
        b_model = _fit_ols(Y, exog_xmy)
        direct_effect = float(b_model.params[1])
        path_b = float(b_model.params[2])

        indirect_effect = path_a * path_b
        prop_mediated = (indirect_effect / total_effect) if total_effect != 0 else float('nan')

        # Bootstrap
        rng = np.random.default_rng(self.random_state)
        indirect_boot = np.empty(self.n_bootstrap)
        idx_all = np.arange(n)
        for i in range(self.n_bootstrap):
            idx = rng.choice(idx_all, size=n, replace=True)
            Xb, Mb, Yb = X[idx], M[idx], Y[idx]
            Zb = Z[idx] if Z.shape[1] else Z
            try:
                exog_a = np.column_stack([Xb.reshape(-1, 1), Zb]) if Zb.shape[1] else Xb.reshape(-1, 1)
                a_b = _fit_ols(Mb, exog_a).params[1]
                exog_b = np.column_stack([Xb.reshape(-1, 1), Mb.reshape(-1, 1), Zb]) if Zb.shape[1] else np.column_stack([Xb.reshape(-1, 1), Mb.reshape(-1, 1)])
                b_b = _fit_ols(Yb, exog_b).params[2]
                indirect_boot[i] = a_b * b_b
            except Exception:
                indirect_boot[i] = np.nan

        indirect_boot = indirect_boot[~np.isnan(indirect_boot)]
        alpha_boot = 1 - self.ci_level
        ci_low = float(np.percentile(indirect_boot, 100 * alpha_boot / 2))
        ci_high = float(np.percentile(indirect_boot, 100 * (1 - alpha_boot / 2)))
        p_value = float(2 * min((indirect_boot >= 0).mean(), (indirect_boot <= 0).mean()))

        # Output
        result = {
            'total_effect': total_effect,
            'direct_effect': direct_effect,
            'indirect_effect': indirect_effect,
            'path_a': path_a,
            'path_b': path_b,
            'prop_mediated': float(prop_mediated) if not np.isnan(prop_mediated) else None,
            'indirect_ci_low': ci_low,
            'indirect_ci_high': ci_high,
            'indirect_p_value': p_value,
            'n_obs': int(n),
            'n_bootstrap_valid': int(len(indirect_boot)),
            'ci_level': float(self.ci_level),
            'method': 'Baron-Kenny + Bootstrap',
        }

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / CONTRACT.output_files['mediation_results_csv']
        pd.DataFrame([result]).to_csv(path, index=False)

        result['mediation_results_csv'] = str(path)
        return result


def get_solver(n_bootstrap: int = 1000, ci_level: float = 0.95,
               random_state: int = 42):
    return MediationAnalysisSolver(
        n_bootstrap=n_bootstrap, ci_level=ci_level,
        random_state=random_state)


def selftest():
    '''Generate synthetic X->M->Y data and verify bootstrap CI covers true indirect effect.'''
    import tempfile
    rng = np.random.default_rng(42)
    n = 200
    X = rng.normal(0, 1, n)
    M = 0.6 * X + rng.normal(0, 0.7, n)  # a ≈ 0.6
    Y = 0.3 * X + 0.5 * M + rng.normal(0, 0.5, n)  # c'≈0.3, b≈0.5
    # True indirect = 0.6*0.5 = 0.3
    df = pd.DataFrame({'x': X, 'm': M, 'y': Y})
    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        s = get_solver(n_bootstrap=1000)
        out = s.run(df, ColumnMapping({'x_col': 'x', 'm_col': 'm', 'y_col': 'y'}), tmp)
        if not (out['indirect_ci_low'] <= 0.3 <= out['indirect_ci_high']):
            diffs.append('CI [{:.4f}, {:.4f}] does not cover true indirect=0.3'.format(out["indirect_ci_low"], out["indirect_ci_high"]))
        if out['indirect_p_value'] > 0.01:
            diffs.append('p-value {:.6f} not significant for strong mediation'.format(out["indirect_p_value"]))
        if abs(out['path_a'] - 0.6) > 0.15:
            diffs.append('path_a={:.4f} far from true 0.6'.format(out["path_a"]))
        if abs(out['path_b'] - 0.5) > 0.15:
            diffs.append('path_b={:.4f} far from true 0.5'.format(out["path_b"]))
    return {
        'ok': len(diffs) == 0,
        'summary': 'mediation bootstrap CI covers true indirect effect' if not diffs else f'{len(diffs)} mismatch(es)',
        'details': {'diffs': diffs, 'tested': ['mediation_analysis']},
    }
