import numpy as np
import pandas as pd
import pytest
import woodwork as ww

from evalml.automl import get_default_primary_search_objective
from evalml.data_checks import (
    DataCheckActionCode,
    DataCheckActionOption,
    DataCheckError,
    DataCheckMessageCode,
    DataChecks,
    DataCheckWarning,
    DCAOParameterType,
    InvalidTargetDataCheck,
)
from evalml.exceptions import DataCheckInitError
from evalml.objectives import (
    MeanSquaredLogError,
    RootMeanSquaredLogError,
)
from evalml.problem_types import ProblemTypes, is_binary, is_multiclass, is_regression
from evalml.utils.woodwork_utils import numeric_and_boolean_ww

invalid_targets_data_check_name = InvalidTargetDataCheck.name


def test_invalid_target_data_check_invalid_n_unique():
    with pytest.raises(
        ValueError,
        match="`n_unique` must be a non-negative integer value.",
    ):
        InvalidTargetDataCheck(
            "regression",
            get_default_primary_search_objective("regression"),
            n_unique=-1,
        )


@pytest.mark.parametrize("null_strategy", ["invalid", None])
def test_invalid_target_data_check_invalid_null_strategy(null_strategy):
    with pytest.raises(
        ValueError,
        match="The acceptable values for 'null_strategy' are 'impute' and 'drop'.",
    ):
        InvalidTargetDataCheck(
            "regression",
            get_default_primary_search_objective("regression"),
            null_strategy=null_strategy,
        )


def test_invalid_target_data_check_nan_error():
    X = pd.DataFrame({"col": [1, 2, 3]})
    invalid_targets_check = InvalidTargetDataCheck(
        "regression",
        get_default_primary_search_objective("regression"),
    )

    assert invalid_targets_check.validate(X, y=pd.Series([1, 2, 3])) == []
    assert invalid_targets_check.validate(X, y=pd.Series([np.nan, np.nan, np.nan])) == [
        DataCheckError(
            message="Target is either empty or fully null.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_IS_EMPTY_OR_FULLY_NULL,
            details={},
        ).to_dict(),
    ]


def test_invalid_target_data_check_numeric_binary_classification_valid_float():
    y = pd.Series([0.0, 1.0, 0.0, 1.0])
    X = pd.DataFrame({"col": range(len(y))})
    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
    )
    assert invalid_targets_check.validate(X, y) == []


def test_invalid_target_data_check_multiclass_two_examples_per_class():
    y = pd.Series([0] + [1] * 19 + [2] * 80)
    X = pd.DataFrame({"col": range(len(y))})
    invalid_targets_check = InvalidTargetDataCheck(
        "multiclass",
        get_default_primary_search_objective("binary"),
    )
    expected_message = "Target does not have at least two instances per class which is required for multiclass classification"

    # with 1 class not having min 2 instances
    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message=expected_message,
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_MULTICLASS_NOT_TWO_EXAMPLES_PER_CLASS,
            details={"least_populated_class_labels": [0]},
        ).to_dict(),
    ]

    y = pd.Series([0] + [1] + [2] * 98)
    X = pd.DataFrame({"col": range(len(y))})
    # with 2 classes not having min 2 instances
    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message=expected_message,
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_MULTICLASS_NOT_TWO_EXAMPLES_PER_CLASS,
            details={"least_populated_class_labels": [0, 1]},
        ).to_dict(),
    ]


@pytest.mark.parametrize(
    "pd_type",
    ["int16", "int32", "int64", "float16", "float32", "float64", "bool"],
)
def test_invalid_target_data_check_invalid_pandas_data_types_error(pd_type):
    y = pd.Series([0, 1, 0, 0, 1, 0, 1, 0])
    y = y.astype(pd_type)
    X = pd.DataFrame({"col": range(len(y))})

    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
    )

    assert invalid_targets_check.validate(X, y) == []

    y = pd.Series(pd.date_range("2000-02-03", periods=5, freq="W"))
    X = pd.DataFrame({"col": range(len(y))})

    unique_values = y.value_counts().index.tolist()
    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message="Target is unsupported {} type. Valid Woodwork logical types include: {}".format(
                "Datetime",
                ", ".join([ltype for ltype in numeric_and_boolean_ww]),
            ),
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_UNSUPPORTED_TYPE,
            details={"unsupported_type": "datetime"},
        ).to_dict(),
        DataCheckError(
            message="Binary class targets require exactly two unique values.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_BINARY_NOT_TWO_UNIQUE_VALUES,
            details={"target_values": unique_values},
        ).to_dict(),
    ]


def test_invalid_target_y_none():
    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
    )
    assert invalid_targets_check.validate(pd.DataFrame(), y=None) == [
        DataCheckError(
            message="Target is None",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_IS_NONE,
            details={},
        ).to_dict(),
    ]


@pytest.mark.parametrize("null_strategy", ["Impute", "DROP"])
def test_invalid_target_data_null_strategies(null_strategy):
    invalid_targets_check = InvalidTargetDataCheck(
        "regression",
        get_default_primary_search_objective("regression"),
        null_strategy=null_strategy,
    )

    expected_action_options = []
    impute_action_option = DataCheckActionOption(
        DataCheckActionCode.IMPUTE_COL,
        data_check_name=invalid_targets_data_check_name,
        parameters={
            "impute_strategy": {
                "parameter_type": DCAOParameterType.GLOBAL,
                "type": "category",
                "categories": ["mean", "most_frequent"],
                "default_value": "mean",
            },
        },
        metadata={"is_target": True},
    )
    drop_action_option = DataCheckActionOption(
        DataCheckActionCode.DROP_ROWS,
        data_check_name=invalid_targets_data_check_name,
        metadata={"is_target": True, "rows": [0, 3]},
    )
    if null_strategy.lower() == "impute":
        expected_action_options.append(impute_action_option)
    elif null_strategy.lower() == "drop":
        expected_action_options.append(drop_action_option)

    expected = [
        DataCheckError(
            message="2 row(s) (40.0%) of target values are null",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_HAS_NULL,
            details={"num_null_rows": 2, "pct_null_rows": 40.0, "rows": [0, 3]},
            action_options=expected_action_options,
        ).to_dict(),
    ]

    y = pd.Series([None, 3.5, 2.8, None, 0])
    X = pd.DataFrame({"col": range(len(y))})

    messages = invalid_targets_check.validate(X, y)
    assert messages == expected


def test_invalid_target_data_input_formats():
    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
        null_strategy="impute",
    )

    # test empty pd.Series
    X = pd.DataFrame()
    messages = invalid_targets_check.validate(X, pd.Series())
    assert messages == [
        DataCheckError(
            message="Target is either empty or fully null.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_IS_EMPTY_OR_FULLY_NULL,
            details={},
        ).to_dict(),
    ]

    expected = [
        DataCheckError(
            message="3 row(s) (75.0%) of target values are null",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_HAS_NULL,
            details={"num_null_rows": 3, "pct_null_rows": 75, "rows": [0, 1, 2]},
            action_options=[
                DataCheckActionOption(
                    DataCheckActionCode.IMPUTE_COL,
                    data_check_name=invalid_targets_data_check_name,
                    parameters={
                        "impute_strategy": {
                            "parameter_type": DCAOParameterType.GLOBAL,
                            "type": "category",
                            "categories": ["most_frequent"],
                            "default_value": "most_frequent",
                        },
                    },
                    metadata={"is_target": True},
                ),
            ],
        ).to_dict(),
        DataCheckError(
            message="Binary class targets require exactly two unique values.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_BINARY_NOT_TWO_UNIQUE_VALUES,
            details={"target_values": [0]},
        ).to_dict(),
    ]
    #  test Woodwork
    y = pd.Series([None, None, None, 0])
    X = pd.DataFrame({"col": range(len(y))})

    messages = invalid_targets_check.validate(X, y)
    assert messages == expected

    #  test list
    y = [np.nan, np.nan, np.nan, 0]
    X = pd.DataFrame({"col": range(len(y))})

    messages = invalid_targets_check.validate(X, y)
    assert messages == expected

    # test np.array
    y = np.array([np.nan, np.nan, np.nan, 0])
    X = pd.DataFrame({"col": range(len(y))})

    messages = invalid_targets_check.validate(X, y)
    assert messages == expected


@pytest.mark.parametrize(
    "problem_type",
    [ProblemTypes.BINARY, ProblemTypes.TIME_SERIES_BINARY],
)
def test_invalid_target_data_check_n_unique(problem_type):
    y = pd.Series(list(range(100, 200)) + list(range(200)))
    unique_values = y.value_counts().index.tolist()[:100]  # n_unique defaults to 100
    X = pd.DataFrame({"col": range(len(y))})

    invalid_targets_check = InvalidTargetDataCheck(
        problem_type,
        get_default_primary_search_objective(problem_type),
    )
    # Test default value of n_unique
    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message="Binary class targets require exactly two unique values.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_BINARY_NOT_TWO_UNIQUE_VALUES,
            details={"target_values": unique_values},
        ).to_dict(),
    ]

    # Test number of unique values < n_unique
    y = pd.Series(range(20))
    X = pd.DataFrame({"col": range(len(y))})

    unique_values = y.value_counts().index.tolist()
    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message="Binary class targets require exactly two unique values.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_BINARY_NOT_TWO_UNIQUE_VALUES,
            details={"target_values": unique_values},
        ).to_dict(),
    ]

    # Test n_unique is None
    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
        n_unique=None,
    )
    y = pd.Series(range(150))
    X = pd.DataFrame({"col": range(len(y))})

    unique_values = y.value_counts().index.tolist()
    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message="Binary class targets require exactly two unique values.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_BINARY_NOT_TWO_UNIQUE_VALUES,
            details={"target_values": unique_values},
        ).to_dict(),
    ]


@pytest.mark.parametrize(
    "objective",
    [
        "Root Mean Squared Log Error",
        "Mean Squared Log Error",
    ],
)
def test_invalid_target_data_check_invalid_labels_for_nonnegative_objective_names(
    objective,
):
    X = pd.DataFrame({"column_one": [100, 200, 100, 200, 200, 100, 200, 100] * 25})
    y = pd.Series([2, 2, 3, 3, -1, -1, 1, 1] * 25)

    data_checks = DataChecks(
        [InvalidTargetDataCheck],
        {
            "InvalidTargetDataCheck": {
                "problem_type": "multiclass",
                "objective": objective,
            },
        },
    )
    assert data_checks.validate(X, y) == [
        DataCheckError(
            message=f"Target has non-positive values which is not supported for {objective}",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_INCOMPATIBLE_OBJECTIVE,
            details={
                "Count of offending values": sum(
                    val <= 0 for val in y.values.flatten()
                ),
            },
        ).to_dict(),
    ]

    X = pd.DataFrame({"column_one": [100, 200, 100, 200, 100]})
    y = pd.Series([2, 3, 0, 1, 1])

    invalid_targets_check = InvalidTargetDataCheck(
        problem_type="regression",
        objective=objective,
    )

    assert invalid_targets_check.validate(X, y) == [
        DataCheckError(
            message=f"Target has non-positive values which is not supported for {objective}",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_INCOMPATIBLE_OBJECTIVE,
            details={
                "Count of offending values": sum(
                    val <= 0 for val in y.values.flatten()
                ),
            },
        ).to_dict(),
    ]


@pytest.mark.parametrize(
    "objective",
    [RootMeanSquaredLogError(), MeanSquaredLogError()],
)
def test_invalid_target_data_check_invalid_labels_for_nonnegative_objective_instances(
    objective,
):
    X = pd.DataFrame({"column_one": [100, 200, 100, 200, 200, 100, 200, 100] * 25})
    y = pd.Series([2, 2, 3, 3, -1, -1, 1, 1] * 25)

    data_checks = DataChecks(
        [InvalidTargetDataCheck],
        {
            "InvalidTargetDataCheck": {
                "problem_type": "multiclass",
                "objective": objective,
            },
        },
    )

    assert data_checks.validate(X, y) == [
        DataCheckError(
            message=f"Target has non-positive values which is not supported for {objective.name}",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_INCOMPATIBLE_OBJECTIVE,
            details={
                "Count of offending values": sum(
                    val <= 0 for val in y.values.flatten()
                ),
            },
        ).to_dict(),
    ]


def test_invalid_target_data_check_invalid_labels_for_objectives(
    time_series_core_objectives,
):
    X = pd.DataFrame({"column_one": [100, 200, 100, 200, 200, 100, 200, 100] * 25})
    y = pd.Series([2, 2, 3, 3, -1, -1, 1, 1] * 25)

    for objective in time_series_core_objectives:
        if not objective.positive_only:
            data_checks = DataChecks(
                [InvalidTargetDataCheck],
                {
                    "InvalidTargetDataCheck": {
                        "problem_type": "multiclass",
                        "objective": objective,
                    },
                },
            )
            assert data_checks.validate(X, y) == []

    X = pd.DataFrame({"column_one": [100, 200, 100, 200, 100]})
    y = pd.Series([2, 3, 0, 1, 1])

    for objective in time_series_core_objectives:
        if not objective.positive_only:
            invalid_targets_check = InvalidTargetDataCheck(
                problem_type="regression",
                objective=objective,
            )
            assert invalid_targets_check.validate(X, y) == []


@pytest.mark.parametrize(
    "objective",
    [
        "Root Mean Squared Log Error",
        "Mean Squared Log Error",
    ],
)
def test_invalid_target_data_check_valid_labels_for_nonnegative_objectives(objective):
    X = pd.DataFrame({"column_one": [100, 100, 200, 300, 100, 200, 100] * 25})
    y = pd.Series([2, 2, 3, 3, 1, 1, 1] * 25)

    data_checks = DataChecks(
        [InvalidTargetDataCheck],
        {
            "InvalidTargetDataCheck": {
                "problem_type": "multiclass",
                "objective": objective,
            },
        },
    )
    assert data_checks.validate(X, y) == []


def test_invalid_target_data_check_initialize_with_none_objective():
    with pytest.raises(DataCheckInitError, match="Encountered the following error"):
        DataChecks(
            [InvalidTargetDataCheck],
            {
                "InvalidTargetDataCheck": {
                    "problem_type": "multiclass",
                    "objective": None,
                },
            },
        )


@pytest.mark.parametrize(
    "problem_type",
    [ProblemTypes.TIME_SERIES_REGRESSION, ProblemTypes.REGRESSION],
)
def test_invalid_target_data_check_regression_problem_nonnumeric_data(problem_type):
    y_categorical = pd.Series(["Peace", "Is", "A", "Lie"] * 100)
    y_mixed_cat_numeric = pd.Series(["Peace", 2, "A", 4] * 100)
    y_integer = pd.Series([1, 2, 3, 4])
    y_float = pd.Series([1.1, 2.2, 3.3, 4.4])
    y_numeric = pd.Series([1, 2.2, 3, 4.4])

    data_check_error = DataCheckError(
        message="Target data type should be numeric for regression type problems.",
        data_check_name=invalid_targets_data_check_name,
        message_code=DataCheckMessageCode.TARGET_UNSUPPORTED_TYPE_REGRESSION,
        details={},
    ).to_dict()

    invalid_targets_check = InvalidTargetDataCheck(
        problem_type,
        get_default_primary_search_objective(problem_type),
    )
    assert invalid_targets_check.validate(
        X=pd.DataFrame({"col": range(len(y_categorical))}),
        y=y_categorical,
    ) == [data_check_error]
    assert invalid_targets_check.validate(
        X=pd.DataFrame({"col": range(len(y_mixed_cat_numeric))}),
        y=y_mixed_cat_numeric,
    ) == [data_check_error]
    assert (
        invalid_targets_check.validate(
            X=pd.DataFrame({"col": range(len(y_integer))}),
            y=y_integer,
        )
        == []
    )
    assert (
        invalid_targets_check.validate(
            X=pd.DataFrame({"col": range(len(y_float))}),
            y=y_float,
        )
        == []
    )
    assert (
        invalid_targets_check.validate(
            X=pd.DataFrame({"col": range(len(y_numeric))}),
            y=y_numeric,
        )
        == []
    )


@pytest.mark.parametrize(
    "problem_type",
    [ProblemTypes.MULTICLASS, ProblemTypes.TIME_SERIES_MULTICLASS],
)
def test_invalid_target_data_check_multiclass_problem_binary_data(problem_type):
    y_multiclass = pd.Series([1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3] * 25)
    y_binary = pd.Series([0, 1, 1, 1, 0, 0] * 25)

    data_check_error = DataCheckError(
        message="Target has two or less classes, which is too few for multiclass problems.  Consider changing to binary.",
        data_check_name=invalid_targets_data_check_name,
        message_code=DataCheckMessageCode.TARGET_MULTICLASS_NOT_ENOUGH_CLASSES,
        details={"num_classes": len(set(y_binary))},
    ).to_dict()

    invalid_targets_check = InvalidTargetDataCheck(
        "multiclass",
        get_default_primary_search_objective("multiclass"),
    )
    assert (
        invalid_targets_check.validate(
            X=pd.DataFrame({"col": range(len(y_multiclass))}),
            y=y_multiclass,
        )
        == []
    )
    assert invalid_targets_check.validate(
        X=pd.DataFrame({"col": range(len(y_binary))}),
        y=y_binary,
    ) == [data_check_error]


@pytest.mark.parametrize(
    "problem_type",
    [ProblemTypes.MULTICLASS, ProblemTypes.TIME_SERIES_MULTICLASS],
)
def test_invalid_target_data_check_multiclass_problem_almost_continuous_data(
    problem_type,
):
    invalid_targets_check = InvalidTargetDataCheck(
        problem_type,
        get_default_primary_search_objective(problem_type),
    )
    y_multiclass_high_classes = pd.Series(
        list(range(0, 100)) * 3,
    )  # 100 classes, 300 samples, .33 class/sample ratio
    X = pd.DataFrame({"col": range(len(y_multiclass_high_classes))})
    data_check_warning = DataCheckWarning(
        message="Target has a large number of unique values, could be regression type problem.",
        data_check_name=invalid_targets_data_check_name,
        message_code=DataCheckMessageCode.TARGET_MULTICLASS_HIGH_UNIQUE_CLASS,
        details={"class_to_value_ratio": 1 / 3},
    ).to_dict()
    assert invalid_targets_check.validate(X, y=y_multiclass_high_classes) == [
        data_check_warning,
    ]

    y_multiclass_med_classes = pd.Series(
        list(range(0, 5)) * 20,
    )  # 5 classes, 100 samples, .05 class/sample ratio
    X = pd.DataFrame({"col": range(len(y_multiclass_med_classes))})
    data_check_warning = DataCheckWarning(
        message="Target has a large number of unique values, could be regression type problem.",
        data_check_name=invalid_targets_data_check_name,
        message_code=DataCheckMessageCode.TARGET_MULTICLASS_HIGH_UNIQUE_CLASS,
        details={"class_to_value_ratio": 0.05},
    ).to_dict()
    assert invalid_targets_check.validate(X, y=y_multiclass_med_classes) == [
        data_check_warning,
    ]

    y_multiclass_low_classes = pd.Series(
        list(range(0, 3)) * 100,
    )  # 2 classes, 300 samples, .01 class/sample ratio
    X = pd.DataFrame({"col": range(len(y_multiclass_low_classes))})
    assert invalid_targets_check.validate(X, y=y_multiclass_low_classes) == []


def test_invalid_target_data_check_mismatched_indices():
    X = pd.DataFrame({"col": [1, 2, 3]})
    y_same_index = pd.Series([1, 0, 1])
    y_diff_index = pd.Series([0, 1, 0], index=[1, 5, 10])
    y_diff_index_order = pd.Series([0, 1, 0], index=[0, 2, 1])

    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
    )
    assert invalid_targets_check.validate(X=None, y=y_same_index) == []
    assert invalid_targets_check.validate(X, y_same_index) == []

    X_index_missing = list(set(y_diff_index.index) - set(X.index))
    y_index_missing = list(set(X.index) - set(y_diff_index.index))
    assert invalid_targets_check.validate(X, y_diff_index) == [
        DataCheckWarning(
            message="Input target and features have mismatched indices. Details will include the first 10 mismatched indices.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.MISMATCHED_INDICES,
            details={
                "indices_not_in_features": X_index_missing,
                "indices_not_in_target": y_index_missing,
            },
        ).to_dict(),
    ]
    assert invalid_targets_check.validate(X, y_diff_index_order) == [
        DataCheckWarning(
            message="Input target and features have mismatched indices order.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.MISMATCHED_INDICES_ORDER,
            details={},
        ).to_dict(),
    ]

    # Test that we only store ten mismatches when there are more than 10 differences in indices found
    X_large = pd.DataFrame({"col": range(20)})
    y_more_than_ten_diff_indices = pd.Series([0, 1] * 10, index=range(20, 40))
    X_index_missing = list(set(y_more_than_ten_diff_indices.index) - set(X.index))
    y_index_missing = list(set(X_large.index) - set(y_more_than_ten_diff_indices.index))
    assert invalid_targets_check.validate(X_large, y_more_than_ten_diff_indices) == [
        DataCheckWarning(
            message="Input target and features have mismatched indices. Details will include the first 10 mismatched indices.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.MISMATCHED_INDICES,
            details={
                "indices_not_in_features": X_index_missing[:10],
                "indices_not_in_target": y_index_missing[:10],
            },
        ).to_dict(),
    ]


def test_invalid_target_data_check_different_lengths():
    X = pd.DataFrame({"col": [1, 2, 3]})
    y_diff_len = pd.Series([0, 1])
    invalid_targets_check = InvalidTargetDataCheck(
        "binary",
        get_default_primary_search_objective("binary"),
    )
    assert invalid_targets_check.validate(X, y_diff_len) == [
        DataCheckWarning(
            message="Input target and features have different lengths",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.MISMATCHED_LENGTHS,
            details={
                "features_length": len(X.index),
                "target_length": len(y_diff_len.index),
            },
        ).to_dict(),
        DataCheckWarning(
            message="Input target and features have mismatched indices. Details will include the first 10 mismatched indices.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.MISMATCHED_INDICES,
            details={"indices_not_in_features": [], "indices_not_in_target": [2]},
        ).to_dict(),
    ]


@pytest.mark.parametrize(
    "problem_type",
    [ProblemTypes.BINARY, ProblemTypes.TIME_SERIES_BINARY],
)
def test_invalid_target_data_check_numeric_binary_does_not_return_warnings(
    problem_type,
):
    y = pd.Series([1, 5, 1, 5, 1, 1])
    X = pd.DataFrame({"col": range(len(y))})
    invalid_targets_check = InvalidTargetDataCheck(
        problem_type,
        get_default_primary_search_objective(problem_type),
    )
    assert invalid_targets_check.validate(X, y) == []


@pytest.mark.parametrize("use_nullable_types", [True, False])
@pytest.mark.parametrize("problem_type", ProblemTypes.all_problem_types)
def test_invalid_target_data_check_action_for_data_with_null(
    use_nullable_types,
    problem_type,
):
    y = pd.Series([None, None, None, 0, 0, 0, 0, 0, 0, 0])
    if use_nullable_types:
        y = ww.init_series(y, logical_type="IntegerNullable")

    X = pd.DataFrame({"col": range(len(y))})
    invalid_targets_check = InvalidTargetDataCheck(
        problem_type,
        get_default_primary_search_objective(problem_type),
        null_strategy="impute",
    )
    expected = [
        DataCheckError(
            message="3 row(s) (30.0%) of target values are null",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_HAS_NULL,
            details={"num_null_rows": 3, "pct_null_rows": 30.0, "rows": [0, 1, 2]},
            action_options=[
                DataCheckActionOption(
                    DataCheckActionCode.IMPUTE_COL,
                    data_check_name=invalid_targets_data_check_name,
                    parameters={
                        "impute_strategy": {
                            "parameter_type": DCAOParameterType.GLOBAL,
                            "type": "category",
                            "categories": ["mean", "most_frequent"]
                            if is_regression(problem_type)
                            else ["most_frequent"],
                            "default_value": "mean"
                            if is_regression(problem_type)
                            else "most_frequent",
                        },
                    },
                    metadata={"is_target": True},
                ),
            ],
        ).to_dict(),
    ]
    if is_binary(problem_type):
        expected.append(
            DataCheckError(
                message="Binary class targets require exactly two unique values.",
                data_check_name=invalid_targets_data_check_name,
                message_code=DataCheckMessageCode.TARGET_BINARY_NOT_TWO_UNIQUE_VALUES,
                details={"target_values": [0]},
            ).to_dict(),
        )
    elif is_multiclass(problem_type):
        expected.append(
            DataCheckError(
                message="Target has two or less classes, which is too few for multiclass problems.  Consider changing to binary.",
                data_check_name=invalid_targets_data_check_name,
                message_code=DataCheckMessageCode.TARGET_MULTICLASS_NOT_ENOUGH_CLASSES,
                details={"num_classes": 1},
            ).to_dict(),
        )
        expected.append(
            DataCheckWarning(
                message="Target has a large number of unique values, could be regression type problem.",
                data_check_name=invalid_targets_data_check_name,
                message_code=DataCheckMessageCode.TARGET_MULTICLASS_HIGH_UNIQUE_CLASS,
                details={"class_to_value_ratio": 0.1},
            ).to_dict(),
        )

    messages = invalid_targets_check.validate(X, y)
    assert messages == expected


@pytest.mark.parametrize("problem_type", ProblemTypes.all_problem_types)
def test_invalid_target_data_check_action_for_all_null(problem_type):
    invalid_targets_check = InvalidTargetDataCheck(
        problem_type,
        get_default_primary_search_objective(problem_type),
    )

    y_all_null = pd.Series([None, None, None])
    X = pd.DataFrame({"col": range(len(y_all_null))})

    expected = [
        DataCheckError(
            message="Target is either empty or fully null.",
            data_check_name=invalid_targets_data_check_name,
            message_code=DataCheckMessageCode.TARGET_IS_EMPTY_OR_FULLY_NULL,
            details={},
        ).to_dict(),
    ]
    messages = invalid_targets_check.validate(X, y_all_null)
    assert messages == expected
