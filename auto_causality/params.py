from flaml import tune
from copy import deepcopy
from typing import Optional, Sequence, Union, Iterable, Dict
from dataclasses import dataclass, field

import warnings
from econml.inference import BootstrapInference  # noqa F401
from sklearn import linear_model


@dataclass
class EstimatorConfig:
    init_params: dict = field(default_factory=dict)
    fit_params: dict = field(default_factory=dict)
    search_space: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    supports_multivalue: bool = False
    experimental: bool = False
    inference: str = "bootstrap"


class SimpleParamService:
    def __init__(
        self,
        propensity_model,
        outcome_model,
        multivalue: bool,
        final_model=None,
        n_bootstrap_samples: Optional[int] = None,
        n_jobs: Optional[int] = None,
        include_experimental=False,
    ):
        self.propensity_model = propensity_model
        self.outcome_model = outcome_model
        self.final_model = final_model
        self.n_jobs = n_jobs
        self.include_experimental = include_experimental
        self.n_bootstrap_samples = n_bootstrap_samples
        self.multivalue = multivalue

    def estimator_names_from_patterns(
        self,
        problem: str,
        patterns: Union[Sequence, str],
        data_rows: Optional[int] = None,
    ):
        def problem_match(est_name: str, problem: str) -> bool:
            return est_name.split(".")[0] == problem

        if patterns == "all":
            if data_rows <= 1000:
                return [e for e in self.estimator_names if problem_match(e, problem)]
            else:
                warnings.warn(
                    "Excluding OrthoForests as they can have problems with large datasets"
                )
                return [
                    e
                    for e in self.estimator_names
                    if ("OrthoForest" not in e) and (problem_match(e, problem))
                ]

        elif patterns == "cheap_inference":
            cfgs = self._configs()
            ests = [
                est
                for est in self.estimator_names
                if cfgs[est].inference != "bootstrap" and problem_match(est, problem)
            ]
            if data_rows <= 1000:
                return ests
            else:
                warnings.warn(
                    "Excluding OrthoForests as they can have problems with large datasets"
                )
                return [e for e in ests if ("OrthoForest" not in e)]

        elif patterns == "auto":
            if problem == "backdoor":
                if self.multivalue:
                    patterns = ["LinearDML"]
                else:
                    # These are the ones we've seen best results from, empirically,
                    # plus dummy for baseline, and SLearner as that's the simplest possible
                    patterns = [
                        "Dummy",
                        "NewDummy",
                        "SLearner",
                        "DomainAdaptationLearner",
                        "TransformedOutcome",
                        "CausalForestDML",
                        "ForestDRLearner",
                    ]

            elif problem == "iv":
                patterns = [
                    "DMLIV",
                    "LinearDRIV",
                    "OrthoIV",
                    "SparseLinearDRIV",
                    "LinearIntentToTreatDRIV",
                ]
            return self.estimator_names_from_patterns(problem, patterns)
        else:
            try:
                for p in patterns:
                    assert isinstance(p, str)
            except Exception:
                raise ValueError(
                    "Invalid estimator list, must be 'auto', 'all', or a list of strings"
                )

            out = [
                est
                for p in patterns
                for est in self.estimator_names
                if p in est and problem_match(est, problem)
            ]
            return sorted(list(set(out)))

    @property
    def estimator_names(self):
        cfgs = self._configs()
        if self.multivalue:
            cfgs = {k: v for k, v in cfgs.items() if v.supports_multivalue}

        if self.include_experimental:
            return list(cfgs.keys())
        else:
            return [est for est, cfg in cfgs.items() if not cfg.experimental]

    def search_space(self, estimator_list: Iterable[str]):
        """constructs search space with estimators and their respective configs

        Returns:
            dict: hierarchical search space
        """
        search_space = [
            {
                "estimator_name": est,
                **est_params.search_space,
            }
            for est, est_params in self._configs().items()
            if est in estimator_list
        ]

        return {"estimator": tune.choice(search_space)}

    def default_configs(self, estimator_list: Iterable[str]):
        """creates list with initial configs to try before moving
        on to hierarchical HPO.
        The list has been identified by evaluating performance of all
        learners on a range of datasets (and metrics).
        Each entry is a dictionary with a learner and its best-performing
        hyper params
        TODO: identify best_performers for list below
        Returns:
            list: list of dicts with promising initial configs
        """
        points = [
            {"estimator": {"estimator_name": est, **est_params.defaults}}
            for est, est_params in self._configs().items()
            if est in estimator_list
        ]

        print("Initial configs:", points)
        return points

    def method_params(
        self,
        estimator: str,
    ):
        return self._configs()[estimator]

    def _configs(self) -> Dict[str, EstimatorConfig]:
        propensity_model = deepcopy(self.propensity_model)
        outcome_model = deepcopy(self.outcome_model)
        if self.n_bootstrap_samples is not None:
            # TODO Egor please look into this
            # bootstrap is causing recursion errors (see notes below)
            # bootstrap = BootstrapInference(
            #     n_bootstrap_samples=self.n_bootstrap_samples, n_jobs=self.n_jobs
            # )
            pass

        if self.final_model is None:
            final_model = deepcopy(self.outcome_model)
        else:
            final_model = deepcopy(self.final_model)

        configs: dict[str:EstimatorConfig] = {
            "backdoor.auto_causality.models.NaiveDummy": EstimatorConfig(),
            "backdoor.auto_causality.models.Dummy": EstimatorConfig(
                init_params={"propensity_model": propensity_model},
                experimental=False,
            ),
            "backdoor.propensity_score_weighting": EstimatorConfig(
                init_params={"propensity_model": propensity_model},
                experimental=True,
            ),
            "backdoor.econml.metalearners.SLearner": EstimatorConfig(
                init_params={"overall_model": outcome_model},
                supports_multivalue=True,
            ),
            "backdoor.econml.metalearners.TLearner": EstimatorConfig(
                init_params={"models": outcome_model},
                supports_multivalue=True,
            ),
            "backdoor.econml.metalearners.XLearner": EstimatorConfig(
                init_params={
                    "propensity_model": propensity_model,
                    "models": outcome_model,
                },
                supports_multivalue=True,
            ),
            "backdoor.econml.metalearners.DomainAdaptationLearner": EstimatorConfig(
                init_params={
                    "propensity_model": propensity_model,
                    "models": outcome_model,
                    "final_models": final_model,
                },
                supports_multivalue=True,
            ),
            "backdoor.econml.dr.ForestDRLearner": EstimatorConfig(
                init_params={
                    "model_propensity": propensity_model,
                    "model_regression": outcome_model,
                    # putting these here for now, until default values can be reconciled with search space
                    "mc_iters": None,
                    "max_depth": None,
                },
                search_space={
                    "min_propensity": tune.loguniform(1e-6, 1e-1),
                    # "mc_iters": tune.randint(0, 10), # is this worth searching over?
                    "n_estimators": tune.randint(2, 200),
                    # "max_depth": is tune.choice([None, tune.randint(2, 1000)]) the right syntax?
                    "min_samples_split": tune.randint(2, 20),
                    "min_samples_leaf": tune.randint(1, 25),
                    "min_weight_fraction_leaf": tune.uniform(0, 0.5),
                    "max_features": tune.choice(["auto", "sqrt", "log2", None]),
                    "min_impurity_decrease": tune.uniform(0, 10),
                    "max_samples": tune.uniform(1e-6, 0.5),
                    "min_balancedness_tol": tune.uniform(0, 0.5),
                    "honest": tune.choice([0, 1]),
                    "subforest_size": tune.randint(2, 10),
                },
                defaults={
                    "min_propensity": 1e-6,
                    "n_estimators": 100,  # this is 1000 by default?
                    "min_samples_split": 5,
                    "min_samples_leaf": 5,
                    "min_weight_fraction_leaf": 0.0,
                    "max_features": "auto",
                    "min_impurity_decrease": 0.0,
                    "max_samples": 0.45,
                    "min_balancedness_tol": 0.45,
                    "honest": True,
                    "subforest_size": 4,
                },
                supports_multivalue=True,
                inference="blb",
            ),
            "backdoor.econml.dr.LinearDRLearner": EstimatorConfig(
                init_params={
                    "model_propensity": propensity_model,
                    "model_regression": outcome_model,
                    "mc_iters": None,
                },
                search_space={
                    "fit_cate_intercept": tune.choice([0, 1]),
                    "min_propensity": tune.loguniform(1e-6, 1e-1),
                    # "mc_iters": tune.randint(0, 10),
                },
                defaults={
                    "fit_cate_intercept": True,
                    "min_propensity": 1e-6,
                },
                supports_multivalue=True,
                inference="auto",
            ),
            "backdoor.econml.dr.SparseLinearDRLearner": EstimatorConfig(
                init_params={
                    "model_propensity": propensity_model,
                    "model_regression": outcome_model,
                    "mc_iters": None,
                },
                search_space={
                    "fit_cate_intercept": tune.choice([0, 1]),
                    "n_alphas": tune.lograndint(1, 1000),
                    "n_alphas_cov": tune.lograndint(1, 100),
                    "min_propensity": tune.loguniform(1e-6, 1e-1),
                    # "mc_iters": tune.randint(0, 10),
                    "tol": tune.qloguniform(1e-7, 1, 1e-7),
                    "max_iter": tune.qlograndint(100, 100000, 100),
                    "mc_agg": tune.choice(["mean", "median"]),
                },
                defaults={
                    "fit_cate_intercept": True,
                    "n_alphas": 100,
                    "n_alphas_cov": 10,
                    "min_propensity": 1e-6,
                    "tol": 0.0001,
                    "max_iter": 10000,
                    "mc_agg": "mean",
                },
                supports_multivalue=True,
                inference="auto",
            ),
            "backdoor.econml.dml.LinearDML": EstimatorConfig(
                init_params={
                    "model_t": propensity_model,
                    "model_y": outcome_model,
                    "discrete_treatment": True,
                    # it runs out of memory fast if the below is not set
                    "linear_first_stages": False,
                    "mc_iters": None,
                },
                search_space={
                    "fit_cate_intercept": tune.choice([0, 1]),
                    # "mc_iters": tune.randint(0, 10),
                    "mc_agg": tune.choice(["mean", "median"]),
                },
                defaults={
                    "fit_cate_intercept": True,
                    "mc_agg": "mean",
                },
                supports_multivalue=True,
                inference="statsmodels",
            ),
            "backdoor.econml.dml.SparseLinearDML": EstimatorConfig(
                init_params={
                    "model_t": propensity_model,
                    "model_y": outcome_model,
                    "discrete_treatment": True,
                    # it runs out of memory fast if the below is not set
                    "linear_first_stages": False,
                    "mc_iters": None,
                },
                search_space={
                    "fit_cate_intercept": tune.choice([0, 1]),
                    # "mc_iters": tune.randint(0, 10),
                    "n_alphas": tune.lograndint(1, 1000),
                    "n_alphas_cov": tune.lograndint(1, 100),
                    "tol": tune.qloguniform(1e-7, 1, 1e-7),
                    "max_iter": tune.qlograndint(100, 100000, 100),
                    "mc_agg": tune.choice(["mean", "median"]),
                },
                defaults={
                    "fit_cate_intercept": True,
                    "n_alphas": 100,
                    "n_alphas_cov": 10,
                    "tol": 0.0001,
                    "max_iter": 10000,
                    "mc_agg": "mean",
                },
                supports_multivalue=True,
                inference="auto",
            ),
            "backdoor.econml.dml.CausalForestDML": EstimatorConfig(
                init_params={
                    "model_t": propensity_model,
                    "model_y": outcome_model,
                    # "max_depth": self.max_depth,
                    # "n_estimators": self.n_estimators,
                    "discrete_treatment": True,
                    # "inference": False,
                    "mc_iters": None,
                    "max_depth": None,
                    "min_var_fraction_leaf": None,
                },
                search_space={
                    # "mc_iters": tune.randint(0, 10),
                    "drate": tune.choice([0, 1]),
                    "n_estimators": tune.randint(2, 500),
                    "criterion": tune.choice(["mse", "het"]),
                    # "max_depth": tune.randint(2, 1000),
                    "min_samples_split": tune.randint(2, 30),
                    "min_samples_leaf": tune.randint(1, 25),
                    "min_weight_fraction_leaf": tune.uniform(0, 0.5),
                    # "min_var_fraction_leaf": tune.uniform(0, 1),
                    "max_features": tune.choice(["auto", "sqrt", "log2", None]),
                    "min_impurity_decrease": tune.uniform(0, 10),
                    "max_samples": tune.uniform(1e-6, 0.5),
                    "min_balancedness_tol": tune.uniform(0, 0.5),
                    "honest": tune.choice([0, 1]),
                    # "inference": tune.choice([0, 1]),
                    "fit_intercept": tune.choice([0, 1]),
                    # Difficult as needs to be a factor of 'n_estimators'
                    "subforest_size": tune.randint(2, 10),
                },
                defaults={
                    # "mc_iters": tune.randint(0, 10),
                    "drate": True,
                    "n_estimators": 100,
                    "criterion": "mse",
                    "min_samples_split": 10,
                    "min_samples_leaf": 5,
                    "min_weight_fraction_leaf": 0.0,
                    "max_features": "auto",
                    "min_impurity_decrease": 0.0,
                    "max_samples": 0.45,
                    "min_balancedness_tol": 0.45,
                    "honest": True,
                    # "inference": tune.choice([0, 1]),
                    "fit_intercept": True,
                    "subforest_size": 4,
                },
                supports_multivalue=True,
                inference="auto",
            ),
            "backdoor.auto_causality.models.TransformedOutcome": EstimatorConfig(
                init_params={
                    "propensity_model": propensity_model,
                    "outcome_model": outcome_model,
                },
            ),
            # leaving out DML and NonParamDML as they're base classes for the 3
            # above
            #
            # This one breaks when running, need to figure out why
            # "backdoor.econml.dr.DRLearner": {
            #     "init_params": {
            #         "model_propensity": propensity_model,
            #         "model_regression": outcome_model,
            #         "model_final": final_model,
            #     },
            #     "fit_params": {},
            # },
            "backdoor.econml.orf.DROrthoForest": EstimatorConfig(
                init_params={
                    "propensity_model": propensity_model,
                    "model_Y": linear_model.Ridge(
                        alpha=0.01
                    ),  # WeightedLasso(alpha=0.01),  #
                    "n_jobs": self.n_jobs,
                    # "max_depth": self.max_depth,
                    # "n_trees": self.n_estimators,
                    # "min_leaf_size": self.min_leaf_size,
                    "backend": "loky",
                },
                search_space={
                    "n_trees": tune.randint(2, 750),
                    "min_leaf_size": tune.randint(1, 50),
                    "max_depth": tune.randint(2, 1000),
                    "subsample_ratio": tune.uniform(0, 1),
                    # "bootstrap": tune.choice([0, 1]),
                    "lambda_reg": tune.uniform(0, 1),
                },
                defaults={
                    "n_trees": 500,
                    "min_leaf_size": 10,
                    "max_depth": 10,
                    "subsample_ratio": 0.7,
                    "lambda_reg": 0.01,
                },
                experimental=True,  # OrthoForest estimators are notoriously slow
                supports_multivalue=True,
                inference="blb",
            ),
            "backdoor.econml.orf.DMLOrthoForest": EstimatorConfig(
                init_params={
                    "model_T": propensity_model,
                    "model_Y": linear_model.Ridge(
                        alpha=0.01
                    ),  # WeightedLasso(alpha=0.01),  #
                    "discrete_treatment": True,
                    "n_jobs": self.n_jobs,
                    # "max_depth": self.max_depth,
                    # "n_trees": self.n_estimators,
                    # "min_leaf_size": self.min_leaf_size,
                    # Loky was running out of disk space for some reason
                    "backend": "threading",
                },
                search_space={
                    "n_trees": tune.randint(2, 750),
                    "min_leaf_size": tune.randint(1, 50),
                    "max_depth": tune.randint(2, 1000),
                    "subsample_ratio": tune.uniform(0, 1),
                    # "bootstrap": tune.choice([0, 1]),
                    "lambda_reg": tune.uniform(0, 1),
                },
                defaults={
                    "n_trees": 500,
                    "min_leaf_size": 10,
                    "max_depth": 10,
                    "subsample_ratio": 0.7,
                    # "bootstrap": tune.choice([0, 1]),
                    "lambda_reg": 0.01,
                },
                experimental=True,  # OrthoForest estimators are notoriously slow
                supports_multivalue=True,
                inference="blb",
            ),
            "iv.econml.iv.dr.LinearDRIV": EstimatorConfig(
                init_params={
                    "model_y_xw": outcome_model,
                    "model_t_xw": propensity_model,
                },
                search_space={
                    "projection": tune.choice([0, 1]),
                },
                defaults={"projection": True},
            ),
            "iv.econml.iv.dml.OrthoIV": EstimatorConfig(
                init_params={
                    "model_y_xw": outcome_model,
                    "model_t_xw": propensity_model,
                },
                search_space={
                    "mc_agg": tune.choice(["mean", "median"]),
                },
                defaults={
                    "mc_agg": "mean",
                },
            ),
            "iv.econml.iv.dml.DMLIV": EstimatorConfig(
                init_params={
                    "model_y_xw": outcome_model,
                    "model_t_xw": propensity_model,
                },
                search_space={
                    "mc_agg": tune.choice(["mean", "median"]),
                },
                defaults={
                    "mc_agg": "mean",
                },
            ),
            "iv.econml.iv.dr.SparseLinearDRIV": EstimatorConfig(
                init_params={
                    "model_y_xw": outcome_model,
                    "model_t_xw": propensity_model,
                },
                search_space={
                    "projection": tune.choice([0, 1]),
                    "opt_reweighted": tune.choice([0, 1]),
                    "cov_clip": tune.quniform(0.08, 0.2, 0.01),
                },
                defaults={
                    "projection": 0,
                    "opt_reweighted": 0,
                    "cov_clip": 0.1,
                },
            ),
            "iv.econml.iv.dr.LinearIntentToTreatDRIV": EstimatorConfig(
                init_params={
                    "model_y_xw": outcome_model,
                },
                search_space={
                    "cov_clip": tune.quniform(0.08, 0.2, 0.01),
                    "opt_reweighted": tune.choice([0, 1]),
                },
                defaults={
                    "cov_clip": 0.1,
                    "opt_reweighted": 1,
                },
            ),
        }
        return configs
