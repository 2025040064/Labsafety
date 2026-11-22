# EXP-83e8f3a3d784481d

Method: rac_hpo

Status: completed

Validation metrics (cycles):

```json
{
  "overall_mae": 23.225607399527334,
  "regimes": {
    "ADOC_10mm": {
      "mae": 11.591154827147633,
      "samples": 135,
      "independent_groups": 2,
      "reliability": "insufficient_independent_groups",
      "independent_unit_kind": "physical_tool",
      "group_errors": [
        {
          "group_id": "101",
          "samples": 84,
          "absolute_error_sum": 671.3552473870693,
          "mae": 7.992324373655587
        },
        {
          "group_id": "5",
          "samples": 51,
          "absolute_error_sum": 893.4506542778614,
          "mae": 17.518640279958067
        }
      ],
      "effective_independent_groups": 2,
      "mae_interval": null,
      "interval_method": null
    },
    "ADOC_5mm": {
      "mae": 29.15259267224906,
      "samples": 265,
      "independent_groups": 2,
      "reliability": "insufficient_independent_groups",
      "independent_unit_kind": "physical_tool",
      "group_errors": [
        {
          "group_id": "8",
          "samples": 116,
          "absolute_error_sum": 2054.1305021216135,
          "mae": 17.70802157001391
        },
        {
          "group_id": "9",
          "samples": 149,
          "absolute_error_sum": 5671.306556024388,
          "mae": 38.06246010754622
        }
      ],
      "effective_independent_groups": 2,
      "mae_interval": null,
      "interval_method": null
    }
  },
  "robust_objective": 0.7393205940801456,
  "worst_normalized_mae": 0.7493509910134715,
  "normalized_regime_mae": {
    "ADOC_10mm": 0.7068942844173833,
    "ADOC_5mm": 0.7493509910134715
  },
  "robustness": {
    "alpha": 0.3,
    "epsilon": 1e-08,
    "weights": {
      "ADOC_10mm": 0.3375,
      "ADOC_5mm": 0.6625
    },
    "weight_source": "explicit",
    "baseline_errors": {
      "ADOC_10mm": 16.39729599685762,
      "ADOC_5mm": 38.90378876436484
    },
    "near_zero_baseline_regimes": []
  }
}
```

Grouping: Author TollIndex denotes 14 physical cutters; one machine, not 968 independent tools
Test data was excluded from all search decisions.
