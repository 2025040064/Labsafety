# EXP-2f144564e6e74c24

Method: tpe

Status: completed

Validation metrics (kWh):

```json
{
  "overall_mae": 13.191677958825139,
  "regimes": {
    "weekday": {
      "mae": 15.136409139073638,
      "samples": 4992,
      "independent_groups": 52,
      "reliability": "descriptive_only"
    },
    "weekend": {
      "mae": 8.135376890179039,
      "samples": 1920,
      "independent_groups": 20,
      "reliability": "descriptive_only"
    }
  },
  "robust_objective": 0.8076869336268973,
  "worst_normalized_mae": 1.027963560522383,
  "normalized_regime_mae": {
    "weekday": 0.5922515512785653,
    "weekend": 1.027963560522383
  },
  "robustness": {
    "alpha": 0.3,
    "epsilon": 1e-08,
    "weights": {
      "weekday": 0.7222222222222222,
      "weekend": 0.2777777777777778
    },
    "weight_source": "explicit",
    "baseline_errors": {
      "weekday": 25.557398879706298,
      "weekend": 7.9140712689905355
    },
    "near_zero_baseline_regimes": []
  }
}
```

Date blocks are temporal groups, not proven independent production batches.
Test data was excluded from all search decisions.
