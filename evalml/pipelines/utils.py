"""Utility methods for EvalML pipelines."""
import copy
import os
import warnings

import black
import featuretools as ft
import pandas as pd
from woodwork import logical_types

from evalml.data_checks import DataCheckActionCode, DataCheckActionOption
from evalml.model_family import ModelFamily
from evalml.pipelines import (
    ComponentGraph,
    MultiseriesRegressionPipeline,
    TimeSeriesBinaryClassificationPipeline,
    TimeSeriesMulticlassClassificationPipeline,
    TimeSeriesRegressionPipeline,
)
from evalml.pipelines.binary_classification_pipeline import BinaryClassificationPipeline
from evalml.pipelines.components import (  # noqa: F401
    CatBoostClassifier,
    CatBoostRegressor,
    ComponentBase,
    DateTimeFeaturizer,
    DFSTransformer,
    DropColumns,
    DropNaNRowsTransformer,
    DropNullColumns,
    DropRowsTransformer,
    EmailFeaturizer,
    Estimator,
    Imputer,
    LogTransformer,
    NaturalLanguageFeaturizer,
    OneHotEncoder,
    OrdinalEncoder,
    Oversampler,
    PerColumnImputer,
    RandomForestClassifier,
    ReplaceNullableTypes,
    SelectColumns,
    StackedEnsembleBase,
    StackedEnsembleClassifier,
    StackedEnsembleRegressor,
    StandardScaler,
    STLDecomposer,
    TargetImputer,
    TimeSeriesFeaturizer,
    TimeSeriesImputer,
    TimeSeriesRegularizer,
    Undersampler,
    URLFeaturizer,
)
from evalml.pipelines.components.transformers.encoders.label_encoder import LabelEncoder
from evalml.pipelines.components.utils import (
    estimator_unable_to_handle_nans,
    get_estimators,
    handle_component_class,
)
from evalml.pipelines.multiclass_classification_pipeline import (
    MulticlassClassificationPipeline,
)
from evalml.pipelines.pipeline_base import PipelineBase
from evalml.pipelines.regression_pipeline import RegressionPipeline
from evalml.problem_types import (
    ProblemTypes,
    handle_problem_types,
    is_classification,
    is_multiseries,
    is_regression,
    is_time_series,
)
from evalml.utils import get_time_index, infer_feature_types
from evalml.utils.cli_utils import get_evalml_black_config
from evalml.utils.gen_utils import contains_all_ts_parameters

DECOMPOSER_PERIOD_CAP = 1000


def _get_label_encoder(X, y, problem_type, estimator_class, sampler_name=None):
    component = []
    if is_classification(problem_type):
        component.append(LabelEncoder)
    return component


def _get_drop_all_null(X, y, problem_type, estimator_class, sampler_name=None):
    component = []
    non_index_unknown = X.ww.select(exclude=["index", "unknown"])
    all_null_cols = non_index_unknown.columns[non_index_unknown.isnull().all()]
    if len(all_null_cols) > 0:
        component.append(DropNullColumns)
    return component


def _get_drop_index_unknown(X, y, problem_type, estimator_class, sampler_name=None):
    component = []
    index_and_unknown_columns = list(
        X.ww.select(["index", "unknown"], return_schema=True).columns,
    )
    if len(index_and_unknown_columns) > 0:
        component.append(DropColumns)
    return component


def _get_email(X, y, problem_type, estimator_class, sampler_name=None):
    components = []
    email_columns = list(X.ww.select("EmailAddress", return_schema=True).columns)
    if len(email_columns) > 0:
        components.append(EmailFeaturizer)

    return components


def _get_url(X, y, problem_type, estimator_class, sampler_name=None):
    components = []

    url_columns = list(X.ww.select("URL", return_schema=True).columns)
    if len(url_columns) > 0:
        components.append(URLFeaturizer)

    return components


def _get_datetime(X, y, problem_type, estimator_class, sampler_name=None):
    components = []
    datetime_cols = list(X.ww.select(["Datetime"], return_schema=True).columns)

    add_datetime_featurizer = len(datetime_cols) > 0
    if add_datetime_featurizer and estimator_class.model_family not in [
        ModelFamily.ARIMA,
        ModelFamily.PROPHET,
    ]:
        components.append(DateTimeFeaturizer)
    return components


def _get_natural_language(X, y, problem_type, estimator_class, sampler_name=None):
    components = []
    text_columns = list(X.ww.select("NaturalLanguage", return_schema=True).columns)
    if len(text_columns) > 0:
        components.append(NaturalLanguageFeaturizer)
    return components


def _get_imputer(X, y, problem_type, estimator_class, sampler_name=None):
    components = []

    input_logical_types = {type(lt) for lt in X.ww.logical_types.values()}
    text_columns = list(X.ww.select("NaturalLanguage", return_schema=True).columns)

    types_imputer_handles = {
        logical_types.AgeNullable,
        logical_types.Boolean,
        logical_types.BooleanNullable,
        logical_types.Categorical,
        logical_types.Double,
        logical_types.Integer,
        logical_types.IntegerNullable,
        logical_types.URL,
        logical_types.EmailAddress,
        logical_types.Datetime,
    }

    if len(input_logical_types.intersection(types_imputer_handles)) or len(
        text_columns,
    ):
        components.append(Imputer)

    return components


def _get_ohe(X, y, problem_type, estimator_class, sampler_name=None):
    components = []

    # The URL and EmailAddress Featurizers will create categorical columns
    categorical_cols = list(
        X.ww.select(
            ["category", "URL", "EmailAddress"],
            return_schema=True,
        ).columns,
    )
    if len(categorical_cols) > 0 and estimator_class not in {
        CatBoostClassifier,
        CatBoostRegressor,
    }:
        components.append(OneHotEncoder)
    return components


def _get_ordinal_encoder(X, y, problem_type, estimator_class, sampler_name=None):
    components = []

    ordinal_cols = list(
        X.ww.select(
            ["Ordinal"],
            return_schema=True,
        ).columns,
    )

    if len(ordinal_cols) > 0:
        components.append(OrdinalEncoder)
    return components


def _get_sampler(X, y, problem_type, estimator_class, sampler_name=None):
    components = []

    sampler_components = {
        "Undersampler": Undersampler,
        "Oversampler": Oversampler,
    }
    if sampler_name is not None:
        components.append(sampler_components[sampler_name])
    return components


def _get_standard_scaler(X, y, problem_type, estimator_class, sampler_name=None):
    components = []
    if estimator_class and estimator_class.model_family == ModelFamily.LINEAR_MODEL:
        components.append(StandardScaler)
    return components


def _get_time_series_featurizer(X, y, problem_type, estimator_class, sampler_name=None):
    components = []
    if is_time_series(problem_type):
        components.append(TimeSeriesFeaturizer)
    return components


def _get_decomposer(X, y, problem_type, estimator_class, sampler_name=None):
    components = []
    if is_time_series(problem_type) and is_regression(problem_type):
        if is_multiseries(problem_type):
            components.append(STLDecomposer)
        else:
            time_index = get_time_index(X, y, None)
            # If the time index frequency is uninferrable, STL will fail
            if time_index.freq is None:
                return components
            freq = time_index.freq.name
            if STLDecomposer.is_freq_valid(freq):
                # Make sure there's a seasonal period
                order = 3 if "Q" in freq else 5
                seasonal_period = STLDecomposer.determine_periodicity(
                    X,
                    y,
                    rel_max_order=order,
                )
                if (
                    seasonal_period is not None
                    and seasonal_period <= DECOMPOSER_PERIOD_CAP
                ):
                    components.append(STLDecomposer)
    return components


def _get_drop_nan_rows_transformer(
    X,
    y,
    problem_type,
    estimator_class,
    sampler_name=None,
):
    components = []
    if is_time_series(problem_type) and (
        estimator_unable_to_handle_nans(estimator_class) or sampler_name
    ):
        components.append(DropNaNRowsTransformer)
    return components


def _get_preprocessing_components(
    X,
    y,
    problem_type,
    estimator_class,
    sampler_name=None,
    exclude_featurizers=None,
    include_decomposer=True,
):
    """Given input data, target data and an estimator class, construct a recommended preprocessing chain to be combined with the estimator and trained on the provided data.

    Args:
        X (pd.DataFrame): The input data of shape [n_samples, n_features].
        y (pd.Series): The target data of length [n_samples].
        problem_type (ProblemTypes or str): Problem type.
        estimator_class (class): A class which subclasses Estimator estimator for pipeline.
        sampler_name (str): The name of the sampler component to add to the pipeline. Defaults to None.
        exclude_featurizers (list[str]): A list of featurizer components to exclude from the returned components.
            Valid options are "DatetimeFeaturizer", "EmailFeaturizer", "URLFeaturizer", "NaturalLanguageFeaturizer", "TimeSeriesFeaturizer"
        include_decomposer (bool): For time series regression problems, whether or not to include a decomposer in the generated pipeline.
            Defaults to True.

    Returns:
        list[Transformer]: A list of applicable preprocessing components to use with the estimator.
    """
    if is_multiseries(problem_type):
        if include_decomposer:
            components_functions = [_get_decomposer]
        else:
            return []

    elif is_time_series(problem_type):
        components_functions = [
            _get_label_encoder,
            _get_drop_all_null,
            _get_drop_index_unknown,
            _get_url,
            _get_email,
            _get_natural_language,
            _get_imputer,
            _get_time_series_featurizer,
        ]
        if include_decomposer:
            components_functions.append(_get_decomposer)
        components_functions = components_functions + [
            _get_datetime,
            _get_ordinal_encoder,
            _get_ohe,
            _get_drop_nan_rows_transformer,
            _get_sampler,
            _get_standard_scaler,
        ]
    else:
        components_functions = [
            _get_label_encoder,
            _get_drop_all_null,
            _get_drop_index_unknown,
            _get_url,
            _get_email,
            _get_datetime,
            _get_natural_language,
            _get_imputer,
            _get_ordinal_encoder,
            _get_ohe,
            _get_sampler,
            _get_standard_scaler,
        ]

    functions_to_exclude = []
    if exclude_featurizers and "DatetimeFeaturizer" in exclude_featurizers:
        functions_to_exclude.append(_get_datetime)
    if exclude_featurizers and "EmailFeaturizer" in exclude_featurizers:
        functions_to_exclude.append(_get_email)
    if exclude_featurizers and "URLFeaturizer" in exclude_featurizers:
        functions_to_exclude.append(_get_url)
    if exclude_featurizers and "NaturalLanguageFeaturizer" in exclude_featurizers:
        functions_to_exclude.append(_get_natural_language)
    if exclude_featurizers and "TimeSeriesFeaturizer" in exclude_featurizers:
        functions_to_exclude.append(_get_time_series_featurizer)

    components = []
    for function in components_functions:
        if function not in functions_to_exclude:
            components.extend(
                function(X, y, problem_type, estimator_class, sampler_name),
            )

    return components


def _get_pipeline_base_class(problem_type):
    """Returns pipeline base class for problem_type."""
    problem_type = handle_problem_types(problem_type)
    if problem_type == ProblemTypes.BINARY:
        return BinaryClassificationPipeline
    elif problem_type == ProblemTypes.MULTICLASS:
        return MulticlassClassificationPipeline
    elif problem_type == ProblemTypes.REGRESSION:
        return RegressionPipeline
    elif problem_type == ProblemTypes.TIME_SERIES_REGRESSION:
        return TimeSeriesRegressionPipeline
    elif problem_type == ProblemTypes.TIME_SERIES_BINARY:
        return TimeSeriesBinaryClassificationPipeline
    elif problem_type == ProblemTypes.TIME_SERIES_MULTICLASS:
        return TimeSeriesMulticlassClassificationPipeline
    else:
        return MultiseriesRegressionPipeline


def _make_pipeline_time_series(
    X,
    y,
    estimator,
    problem_type,
    parameters=None,
    sampler_name=None,
    known_in_advance=None,
    exclude_featurizers=None,
    include_decomposer=True,
    features=False,
):
    """Make a pipeline for time series problems.

    If there are known-in-advance features, the pipeline will have two parallel subgraphs.
    In the first part, the not known_in_advance features will be featurized with a TimeSeriesFeaturizer.
    The known_in_advance features are treated like a non-time-series features since they don't change with time.

    Args:
        X (pd.DataFrame): The input data of shape [n_samples, n_features].
        y (pd.Series): The target data of length [n_samples].
        estimator (Estimator): Estimator for pipeline.
        problem_type (ProblemTypes or str): Problem type for pipeline to generate.
        parameters (dict): Dictionary with component names as keys and dictionary of that component's parameters as values.
            An empty dictionary or None implies using all default values for component parameters.
        sampler_name (str): The name of the sampler component to add to the pipeline. Only used in classification problems.
            Defaults to None
        known_in_advance (list[str], None): List of features that are known in advance.
        exclude_featurizers (list[str]): A list of featurizer components to exclude from the pipeline.
           Valid options are "DatetimeFeaturizer", "EmailFeaturizer", "URLFeaturizer", "NaturalLanguageFeaturizer", "TimeSeriesFeaturizer"
        include_decomposer (bool): For time series regression problems, whether or not to include a decomposer in the generated pipeline.
            Defaults to True.
        features (bool): Whether to add a DFSTransformer component to this pipeline.

    Returns:
        PipelineBase: TimeSeriesPipeline
    """
    if known_in_advance:
        not_known_in_advance = [c for c in X.columns if c not in known_in_advance]
        X_not_known_in_advance = X.ww[not_known_in_advance]
        X_known_in_advance = X.ww[known_in_advance]
    else:
        X_not_known_in_advance = X
        X_known_in_advance = None

    preprocessing_components = _get_preprocessing_components(
        X_not_known_in_advance,
        y,
        problem_type,
        estimator,
        sampler_name,
        exclude_featurizers,
        include_decomposer,
    )

    dfs_transformer = [DFSTransformer] if features else []

    if known_in_advance:
        preprocessing_components = [SelectColumns] + preprocessing_components
        if (
            Oversampler not in preprocessing_components
            and DropNaNRowsTransformer in preprocessing_components
        ):
            preprocessing_components.remove(DropNaNRowsTransformer)
    else:
        preprocessing_components = (
            dfs_transformer + preprocessing_components + [estimator]
        )

    component_graph = PipelineBase._make_component_dict_from_component_list(
        preprocessing_components,
    )
    base_class = _get_pipeline_base_class(problem_type)
    pipeline = base_class(component_graph, parameters=parameters)
    if X_known_in_advance is not None:
        # We can't specify a time series problem type because then the known-in-advance
        # pipeline will have a time series featurizer, which is not what we want.
        # The pre-processing components do not depend on problem type so we
        # are ok by specifying regression for the known-in-advance sub pipeline
        # Since we specify the correct problem type for the not known-in-advance pipeline
        # the label encoder and time series featurizer will be correctly added to the
        # overall pipeline
        kina_preprocessing = [SelectColumns] + _get_preprocessing_components(
            X_known_in_advance,
            y,
            ProblemTypes.REGRESSION,
            estimator,
            sampler_name,
            exclude_featurizers,
            include_decomposer,
        )
        kina_component_graph = PipelineBase._make_component_dict_from_component_list(
            kina_preprocessing,
        )
        need_drop_nan = estimator_unable_to_handle_nans(estimator)
        # Give the known-in-advance pipeline a different name to ensure that it does not have the
        # same name as the other pipeline. Otherwise there could be a clash in the sub_pipeline_names
        # dict below for some estimators that don't have a lot of preprocessing steps, e.g ARIMA
        kina_pipeline = base_class(
            kina_component_graph,
            parameters=parameters,
            custom_name="Pipeline",
        )
        pre_pipeline_components = (
            {"DFS Transformer": ["DFS Transformer", "X", "y"]} if features else None
        )
        pipeline = _make_pipeline_from_multiple_graphs(
            [pipeline, kina_pipeline],
            DropNaNRowsTransformer if need_drop_nan else estimator,
            problem_type,
            parameters=parameters,
            pre_pipeline_components=pre_pipeline_components,
            sub_pipeline_names={
                kina_pipeline.name: "Known In Advance",
                pipeline.name: "Not Known In Advance",
            },
        )
        if need_drop_nan:
            last_component_name = pipeline.component_graph.get_last_component().name
            pipeline.component_graph.component_dict[estimator.name] = [
                estimator,
                last_component_name + ".x",
                last_component_name + ".y",
            ]
        pipeline = pipeline.new(parameters)
    return pipeline


def make_pipeline(
    X,
    y,
    estimator,
    problem_type,
    parameters=None,
    sampler_name=None,
    extra_components_before=None,
    extra_components_after=None,
    use_estimator=True,
    known_in_advance=None,
    features=False,
    exclude_featurizers=None,
    include_decomposer=True,
):
    """Given input data, target data, an estimator class and the problem type, generates a pipeline class with a preprocessing chain which was recommended based on the inputs. The pipeline will be a subclass of the appropriate pipeline base class for the specified problem_type.

    Args:
        X (pd.DataFrame): The input data of shape [n_samples, n_features].
        y (pd.Series): The target data of length [n_samples].
        estimator (Estimator): Estimator for pipeline.
        problem_type (ProblemTypes or str): Problem type for pipeline to generate.
        parameters (dict): Dictionary with component names as keys and dictionary of that component's parameters as values.
            An empty dictionary or None implies using all default values for component parameters.
        sampler_name (str): The name of the sampler component to add to the pipeline. Only used in classification problems.
            Defaults to None
        extra_components_before (list[ComponentBase]): List of extra components to be added before preprocessing components. Defaults to None.
        extra_components_after (list[ComponentBase]): List of extra components to be added after preprocessing components. Defaults to None.
        use_estimator (bool): Whether to add the provided estimator to the pipeline or not. Defaults to True.
        known_in_advance (list[str], None): List of features that are known in advance.
        features (bool): Whether to add a DFSTransformer component to this pipeline.
        exclude_featurizers (list[str]): A list of featurizer components to exclude from the pipeline.
            Valid options are "DatetimeFeaturizer", "EmailFeaturizer", "URLFeaturizer", "NaturalLanguageFeaturizer", "TimeSeriesFeaturizer"
        include_decomposer (bool): For time series regression problems, whether or not to include a decomposer in the generated pipeline.
            Defaults to True.

    Returns:
         PipelineBase object: PipelineBase instance with dynamically generated preprocessing components and specified estimator.

    Raises:
        ValueError: If estimator is not valid for the given problem type, or sampling is not supported for the given problem type.
    """
    X = infer_feature_types(X)
    y = infer_feature_types(y)

    if estimator:
        problem_type = handle_problem_types(problem_type)
        if estimator not in get_estimators(problem_type):
            raise ValueError(
                f"{estimator.name} is not a valid estimator for problem type",
            )
        if not is_classification(problem_type) and sampler_name is not None:
            raise ValueError(
                f"Sampling is unsupported for problem_type {str(problem_type)}",
            )

    if is_time_series(problem_type):
        pipeline = _make_pipeline_time_series(
            X,
            y,
            estimator,
            problem_type,
            parameters,
            sampler_name,
            known_in_advance,
            exclude_featurizers,
            include_decomposer,
            features,
        )
    else:
        preprocessing_components = _get_preprocessing_components(
            X,
            y,
            problem_type,
            estimator,
            sampler_name,
            exclude_featurizers,
        )
        extra_components_before = extra_components_before or []
        extra_components_after = extra_components_after or []
        dfs_transformer = [DFSTransformer] if features else []
        estimator_component = [estimator] if use_estimator else []
        complete_component_list = (
            dfs_transformer
            + extra_components_before
            + preprocessing_components
            + extra_components_after
            + estimator_component
        )

        component_graph = PipelineBase._make_component_dict_from_component_list(
            complete_component_list,
        )
        base_class = _get_pipeline_base_class(problem_type)
        pipeline = base_class(component_graph, parameters=parameters)

    return pipeline


def generate_pipeline_code(element, features_path=None):
    """Creates and returns a string that contains the Python imports and code required for running the EvalML pipeline.

    Args:
        element (pipeline instance): The instance of the pipeline to generate string Python code.
        features_path (str): path to features json created from featuretools.save_features(). Defaults to None.

    Returns:
        str: String representation of Python code that can be run separately in order to recreate the pipeline instance.
        Does not include code for custom component implementation.

    Raises:
        ValueError: If element is not a pipeline, or if the pipeline is nonlinear.
        ValueError: If features in `features_path` do not match the features on the pipeline.
    """
    # hold the imports needed and add code to end
    code_strings = []
    if not isinstance(element, PipelineBase):
        raise ValueError(
            "Element must be a pipeline instance, received {}".format(type(element)),
        )
    if isinstance(element.component_graph, dict):
        raise ValueError("Code generation for nonlinear pipelines is not supported yet")
    code_strings.append(
        "from {} import {}".format(
            element.__class__.__module__,
            element.__class__.__name__,
        ),
    )
    if isinstance(element.estimator, StackedEnsembleBase):
        final_estimator = element.parameters[element.estimator.name]["final_estimator"]
        code_strings.append(
            "from {} import {}".format(
                final_estimator.__class__.__module__,
                final_estimator.__class__.__name__,
            ),
        )
    if element.component_graph.has_dfs and not features_path:
        warnings.warn(
            "This pipeline contains a DFS Transformer but no `features_path` has been specified. Please add a `features_path` or the pipeline code will generate a pipeline that does not run DFS.",
        )
    has_dfs_and_features = element.component_graph.has_dfs and features_path
    if has_dfs_and_features:
        features = ft.load_features(features_path)
        dfs_features = None
        for component in element.parameters:
            if "DFS Transformer" in component:
                dfs_features = element.parameters[component]["features"]
                break
        if len(features) != len(dfs_features):
            raise ValueError(
                "Provided features in `features_path` do not match pipeline features. There is a different amount of features in the loaded features.",
            )

        for pipeline_feature, serialized_feature in zip(
            dfs_features,
            features,
        ):
            if (
                pipeline_feature.get_feature_names()
                != serialized_feature.get_feature_names()
            ):
                raise ValueError(
                    "Provided features in `features_path` do not match pipeline features.",
                )
        code_strings.append("from featuretools import load_features")
        code_strings.append(f'features=load_features("{features_path}")')
    code_strings.append(repr(element))
    pipeline_code = "\n".join(code_strings)
    if has_dfs_and_features:
        pipeline_code = pipeline_code.replace(
            # this open single quote here and below is to match for
            # DFS Transformers in pipelines with sub pipelines i.e default algo pipelines or ensemble pipelines.
            "DFS Transformer':{},",
            "DFS Transformer':{'features':features},",
        )
    current_dir = os.path.dirname(os.path.abspath(__file__))
    evalml_path = os.path.abspath(os.path.join(current_dir, "..", ".."))
    black_config = get_evalml_black_config(evalml_path)
    pipeline_code = black.format_str(pipeline_code, mode=black.Mode(**black_config))
    return pipeline_code


def generate_pipeline_example(
    pipeline,
    path_to_train,
    path_to_holdout,
    target,
    path_to_features=None,
    path_to_mapping="",
    output_file_path=None,
):
    """Creates and returns a string that contains the Python imports and code required for running the EvalML pipeline.

    Args:
        pipeline (pipeline instance): The instance of the pipeline to generate string Python code.
        path_to_train (str): path to training data.
        path_to_holdout (str): path to holdout data.
        target (str): target variable.
        path_to_features (str): path to features json. Defaults to None.
        path_to_mapping (str): path to mapping json. Defaults to None.
        output_file_path (str): path to output python file. Defaults to None.

    Returns:
        str: String representation of Python code that can be run separately in order to recreate the pipeline instance.
        Does not include code for custom component implementation.

    """
    output_str = f"""
import evalml
import woodwork as ww
import pandas as pd

PATH_TO_TRAIN = "{path_to_train}"
PATH_TO_HOLDOUT = "{path_to_holdout}"
TARGET = "{target}"
column_mapping = "{path_to_mapping}"

# This is the machine learning pipeline you have exported.
# By running this code you will fit the pipeline on the files provided
# and you can then use this pipeline for prediction and model understanding.
{generate_pipeline_code(pipeline, path_to_features)}

print(pipeline.name)
print(pipeline.parameters)
pipeline.describe()

df = ww.deserialize.from_disk(PATH_TO_TRAIN)
y_train = df.ww[TARGET]
X_train = df.ww.drop(TARGET)
pipeline.fit(X_train, y_train)

# You can now generate predictions as well as run model understanding.
df = ww.deserialize.from_disk(PATH_TO_HOLDOUT)
y_holdout = df.ww[TARGET]
X_holdout = df.ww.drop(TARGET)
"""
    if not is_time_series(pipeline.problem_type):
        output_str += """
pipeline.predict(X_holdout)

# Note: if you have a column mapping, to predict on new data you have on hand
# Map the column names and run prediction
# X_test = X_test.rename(column_mapping, axis=1)
# pipeline.predict(X_test)

# For more info please check out:
# https://evalml.alteryx.com/en/stable/user_guide/automl.html
"""
    else:
        output_str += """
pipeline.predict(X_holdout, X_train=X_train, y_train=y_train)

# Note: if you have a column mapping, to predict on new data you have on hand
# Map the column names and run prediction
# X_test = X_test.rename(column_mapping, axis=1)
# pipeline.predict(X_test, X_train=X_train, y_train=y_train)

# For more info please check out:
# https://evalml.alteryx.com/en/stable/user_guide/automl.html
"""

    if output_file_path:
        with open(output_file_path, "w") as text_file:
            text_file.write(output_str)
    return output_str


def _make_stacked_ensemble_pipeline(
    input_pipelines,
    problem_type,
    final_estimator=None,
    n_jobs=-1,
    random_seed=0,
    cached_data=None,
    label_encoder_params=None,
):
    """Creates a pipeline with a stacked ensemble estimator.

    Args:
        input_pipelines (list(PipelineBase or subclass obj)): List of pipeline instances to use as the base estimators for the stacked ensemble.
        problem_type (ProblemType): Problem type of pipeline
        final_estimator (Estimator): Metalearner to use for the ensembler. Defaults to None.
        n_jobs (int or None): Integer describing level of parallelism used for pipelines.
            None and 1 are equivalent. If set to -1, all CPUs are used. For n_jobs below -1, (n_cpus + 1 + n_jobs) are used.
            Defaults to -1.
        cached_data (dict): A dictionary of cached data, where the keys are the model family. Expected to be of format
            {model_family: {hash1: trained_component_graph, hash2: trained_component_graph...}...}.
            Defaults to None.
        label_encoder_params (dict): The parameters passed in for the label encoder, used only for classification problems. Defaults to None.
        random_seed (int): Seed for the random number generator. Defaults to 0.

    Returns:
        Pipeline with appropriate stacked ensemble estimator.
    """

    def _make_new_component_name(model_type, component_name, idx=None):
        idx = " " + str(idx) if idx is not None else ""
        return f"{str(model_type)} Pipeline{idx} - {component_name}"

    def _set_cache_data(
        cached_data,
        model_family,
        cached_component_instances,
        new_component_name,
        name,
    ):
        # sets the new cached component dictionary using the cached data and model family information
        if len(cached_data) and model_family in list(cached_data.keys()):
            for hashes, component_instances in cached_data[model_family].items():
                if hashes not in list(cached_component_instances.keys()):
                    cached_component_instances[hashes] = {}
                cached_component_instances[hashes][new_component_name] = cached_data[
                    model_family
                ][hashes][name]

    component_graph = (
        {"Label Encoder": ["Label Encoder", "X", "y"]}
        if is_classification(problem_type)
        else {}
    )
    final_components = []
    used_model_families = []
    parameters = label_encoder_params or {}
    cached_data = cached_data or {}
    if is_classification(problem_type):
        parameters.update(
            {
                "Stacked Ensemble Classifier": {
                    "n_jobs": n_jobs,
                },
            },
        )
        estimator = StackedEnsembleClassifier
        pipeline_name = "Stacked Ensemble Classification Pipeline"
    else:
        parameters = {
            "Stacked Ensemble Regressor": {
                "n_jobs": n_jobs,
            },
        }
        estimator = StackedEnsembleRegressor
        pipeline_name = "Stacked Ensemble Regression Pipeline"

    pipeline_class = {
        ProblemTypes.BINARY: BinaryClassificationPipeline,
        ProblemTypes.MULTICLASS: MulticlassClassificationPipeline,
        ProblemTypes.REGRESSION: RegressionPipeline,
    }[problem_type]

    cached_component_instances = {}
    for pipeline in input_pipelines:
        model_family = pipeline.component_graph[-1].model_family
        model_family_idx = (
            used_model_families.count(model_family) + 1
            if used_model_families.count(model_family) > 0
            else None
        )
        used_model_families.append(model_family)
        final_component = None
        ensemble_y = "y"
        for name, component_list in pipeline.component_graph.component_dict.items():
            new_component_list = []
            new_component_name = _make_new_component_name(
                model_family,
                name,
                model_family_idx,
            )

            _set_cache_data(
                cached_data,
                model_family,
                cached_component_instances,
                new_component_name,
                name,
            )

            for i, item in enumerate(component_list):
                if i == 0:
                    fitted_comp = handle_component_class(item)
                    new_component_list.append(fitted_comp)
                    parameters[new_component_name] = pipeline.parameters.get(name, {})
                elif isinstance(item, str) and item not in ["X", "y"]:
                    new_component_list.append(
                        _make_new_component_name(model_family, item, model_family_idx),
                    )
                elif isinstance(item, str) and item == "y":
                    if is_classification(problem_type):
                        new_component_list.append("Label Encoder.y")
                    else:
                        new_component_list.append("y")
                else:
                    new_component_list.append(item)
                if i != 0 and item.endswith(".y"):
                    ensemble_y = _make_new_component_name(
                        model_family,
                        item,
                        model_family_idx,
                    )
            component_graph[new_component_name] = new_component_list
            final_component = new_component_name
        final_components.append(final_component)

    component_graph[estimator.name] = (
        [estimator] + [comp + ".x" for comp in final_components] + [ensemble_y]
    )
    cg = ComponentGraph(
        component_dict=component_graph,
        cached_data=cached_component_instances,
        random_seed=random_seed,
    )

    return pipeline_class(
        cg,
        parameters=parameters,
        custom_name=pipeline_name,
        random_seed=random_seed,
    )


def _make_pipeline_from_multiple_graphs(
    input_pipelines,
    estimator,
    problem_type,
    parameters=None,
    pipeline_name=None,
    sub_pipeline_names=None,
    pre_pipeline_components=None,
    post_pipelines_components=None,
    random_seed=0,
):
    """Creates a pipeline from multiple preprocessing pipelines and a final estimator. Final y input to the estimator will be chosen from the last of the input pipelines.

    Args:
        input_pipelines (list(PipelineBase or subclass obj)): List of pipeline instances to use for preprocessing.
        estimator (Estimator): Final estimator for the pipelines.
        problem_type (ProblemType): Problem type of pipeline.
        parameters (Dict): Parameters to initialize pipeline with. Defaults to an empty dictionary.
        pipeline_name (str): Custom name for the final pipeline.
        sub_pipeline_names (Dict): Dictionary mapping original input pipeline names to new names. This will be used to rename components. Defaults to None.
        pre_pipeline_components (Dict): Component graph of components preceding the split of multiple graphs. Must be in component graph format, {"Label Encoder": ["Label Encoder", "X", "y"]} and currently restricted to components that only alter X input.
        post_pipelines_components (Dict): Component graph of components before the estimator after the split of multiple graphs. Must be in component graph format, {"Label Encoder": ["Label Encoder", "X", "y"]} and currently restricted to components that only alter X input.
        random_seed (int): Random seed for the pipeline. Defaults to 0.

    Returns:
        pipeline (PipelineBase): Pipeline created with the input pipelines.
    """

    def _make_new_component_name(name, component_name, idx=None, pipeline_name=None):
        idx = " " + str(idx) if idx is not None else ""
        if pipeline_name:
            return f"{pipeline_name} Pipeline{idx} - {component_name}"
        return f"{str(name)} Pipeline{idx} - {component_name}"

    # Without this copy, the parameters will be modified in between
    # invocations of this method.
    parameters = copy.deepcopy(parameters) if parameters else {}
    final_components = []
    used_names = []

    pre_pipeline_components = (
        {} if not pre_pipeline_components else pre_pipeline_components
    )
    last_prior_component = (
        list(pre_pipeline_components.keys())[-1] if pre_pipeline_components else None
    )
    component_graph = pre_pipeline_components
    if is_classification(problem_type):
        component_graph.update({"Label Encoder": ["Label Encoder", "X", "y"]})

    for pipeline in input_pipelines:
        component_pipeline_name = pipeline.name
        name_idx = (
            used_names.count(component_pipeline_name) + 1
            if used_names.count(component_pipeline_name) > 0
            else None
        )
        used_names.append(component_pipeline_name)
        sub_pipeline_name = (
            sub_pipeline_names[pipeline.name] if sub_pipeline_names else None
        )
        final_component = None
        final_y = "y"

        final_y_candidate = (
            None
            if not handle_component_class(
                pipeline.component_graph.compute_order[-1],
            ).modifies_target
            else _make_new_component_name(
                component_pipeline_name,
                pipeline.component_graph.compute_order[-1],
                name_idx,
                sub_pipeline_name,
            )
            + ".y"
        )
        for name, component_list in pipeline.component_graph.component_dict.items():
            new_component_list = []
            new_component_name = _make_new_component_name(
                component_pipeline_name,
                name,
                name_idx,
                sub_pipeline_name,
            )
            first_x_component = (
                pipeline.component_graph.compute_order[0]
                if pipeline.component_graph.compute_order[0] != "Label Encoder"
                else pipeline.component_graph.compute_order[1]
            )
            for i, item in enumerate(component_list):
                if i == 0:
                    fitted_comp = handle_component_class(item)
                    new_component_list.append(fitted_comp)
                    parameters[new_component_name] = pipeline.parameters.get(name, {})
                elif isinstance(item, str) and item not in ["X", "y"]:
                    new_component_list.append(
                        _make_new_component_name(
                            component_pipeline_name,
                            item,
                            name_idx,
                            sub_pipeline_name,
                        ),
                    )
                    if i != 0 and item.endswith(".y"):
                        final_y = _make_new_component_name(
                            component_pipeline_name,
                            item,
                            name_idx,
                            sub_pipeline_name,
                        )
                elif isinstance(item, str) and item == "y":
                    if is_classification(problem_type):
                        new_component_list.append("Label Encoder.y")
                    else:
                        new_component_list.append("y")
                elif name == first_x_component and last_prior_component:
                    # if we have prior components, change the X input from the first component of each sub-pipeline to be the last prior components X output.
                    new_component_list.append(f"{last_prior_component}.x")
                else:
                    new_component_list.append(item)
            component_graph[new_component_name] = new_component_list
            final_component = new_component_name
        final_components.append(final_component)

    final_y = final_y_candidate if final_y_candidate else final_y
    last_pre_estimator_component = None
    if post_pipelines_components:
        first_pre_estimator_component = list(post_pipelines_components.keys())[0]
        post_pipelines_components[first_pre_estimator_component] = (
            [first_pre_estimator_component]
            + [comp + ".x" for comp in final_components]
            + [final_y]
        )
        component_graph.update(post_pipelines_components)
        last_pre_estimator_component = list(post_pipelines_components.keys())[-1]
    if last_pre_estimator_component:
        component_graph[estimator.name] = (
            [estimator]
            + [last_pre_estimator_component + ".x"]
            + [last_pre_estimator_component + ".y"]
        )
    else:
        component_graph[estimator.name] = (
            [estimator] + [comp + ".x" for comp in final_components] + [final_y]
        )
    pipeline_class = {
        ProblemTypes.BINARY: BinaryClassificationPipeline,
        ProblemTypes.MULTICLASS: MulticlassClassificationPipeline,
        ProblemTypes.REGRESSION: RegressionPipeline,
        ProblemTypes.TIME_SERIES_BINARY: TimeSeriesBinaryClassificationPipeline,
        ProblemTypes.TIME_SERIES_MULTICLASS: TimeSeriesMulticlassClassificationPipeline,
        ProblemTypes.TIME_SERIES_REGRESSION: TimeSeriesRegressionPipeline,
    }[problem_type]
    return pipeline_class(
        component_graph,
        parameters=parameters,
        custom_name=pipeline_name,
        random_seed=random_seed,
    )


def make_pipeline_from_actions(problem_type, actions, problem_configuration=None):
    """Creates a pipeline of components to address the input DataCheckAction list.

    Args:
        problem_type (str or ProblemType): The problem type that the pipeline should address.
        actions (list[DataCheckAction]): List of DataCheckAction objects used to create list of components
        problem_configuration (dict): Required for time series problem types. Values should be passed in for time_index, gap, forecast_horizon, and max_delay.

    Returns:
        PipelineBase: Pipeline which can be used to address data check actions.
    """
    component_list = _make_component_list_from_actions(actions)
    parameters = {}
    for component in component_list:
        parameters[component.name] = component.parameters
    component_dict = PipelineBase._make_component_dict_from_component_list(
        [component.name for component in component_list],
    )
    base_class = _get_pipeline_base_class(problem_type)
    if problem_configuration:
        parameters["pipeline"] = problem_configuration
    return base_class(component_dict, parameters=parameters)


def _make_component_list_from_actions(actions):
    """Creates a list of components from the input DataCheckAction list.

    Args:
        actions (list(DataCheckAction)): List of DataCheckAction objects used to create list of components

    Returns:
        list(ComponentBase): List of components used to address the input actions
    """
    components = []
    cols_to_drop = []
    indices_to_drop = []

    for action in actions:
        if action.action_code == DataCheckActionCode.REGULARIZE_AND_IMPUTE_DATASET:
            metadata = action.metadata
            parameters = metadata.get("parameters", {})
            components.extend(
                [
                    TimeSeriesRegularizer(
                        time_index=parameters.get("time_index", None),
                        frequency_payload=parameters["frequency_payload"],
                    ),
                    TimeSeriesImputer(),
                ],
            )
        elif action.action_code == DataCheckActionCode.DROP_COL:
            cols_to_drop.extend(action.metadata["columns"])
        elif action.action_code == DataCheckActionCode.IMPUTE_COL:
            metadata = action.metadata
            parameters = metadata.get("parameters", {})
            if metadata["is_target"]:
                components.append(
                    TargetImputer(impute_strategy=parameters["impute_strategy"]),
                )
            else:
                impute_strategies = parameters["impute_strategies"]
                components.append(PerColumnImputer(impute_strategies=impute_strategies))
        elif action.action_code == DataCheckActionCode.DROP_ROWS:
            indices_to_drop.extend(action.metadata["rows"])
    if cols_to_drop:
        cols_to_drop = sorted(set(cols_to_drop))
        components.append(DropColumns(columns=cols_to_drop))
    if indices_to_drop:
        indices_to_drop = sorted(set(indices_to_drop))
        components.append(DropRowsTransformer(indices_to_drop=indices_to_drop))

    return components


def make_pipeline_from_data_check_output(
    problem_type,
    data_check_output,
    problem_configuration=None,
):
    """Creates a pipeline of components to address warnings and errors output from running data checks. Uses all default suggestions.

    Args:
        problem_type (str or ProblemType): The problem type.
        data_check_output (dict): Output from calling ``DataCheck.validate()``.
        problem_configuration (dict): Required for time series problem types. Values should be passed in for time_index, gap, forecast_horizon, and max_delay.

    Returns:
        PipelineBase: Pipeline which can be used to address data check outputs.

    Raises:
        ValueError: If problem_type is of type time series but an incorrect problem_configuration has been passed.
    """
    action_options = []
    for message in data_check_output:
        action_options.extend([option for option in message["action_options"]])

    if is_time_series(problem_type):
        is_valid, msg = contains_all_ts_parameters(problem_configuration)
        if not is_valid:
            raise ValueError(msg)

    actions = get_actions_from_option_defaults(
        DataCheckActionOption.convert_dict_to_option(option)
        for option in action_options
    )

    return make_pipeline_from_actions(problem_type, actions, problem_configuration)


def get_actions_from_option_defaults(action_options):
    """Returns a list of actions based on the defaults parameters of each option in the input DataCheckActionOption list.

    Args:
        action_options (list[DataCheckActionOption]): List of DataCheckActionOption objects

    Returns:
        list[DataCheckAction]: List of actions based on the defaults parameters of each option in the input list.
    """
    actions = []
    for option in action_options:
        actions.append(option.get_action_from_defaults())
    return actions


def make_timeseries_baseline_pipeline(
    problem_type,
    gap,
    forecast_horizon,
    time_index,
    exclude_featurizer=False,
    series_id=None,
):
    """Make a baseline pipeline for time series regression problems.

    Args:
        problem_type: One of TIME_SERIES_REGRESSION, TIME_SERIES_MULTICLASS, TIME_SERIES_BINARY
        gap (int): Non-negative gap parameter.
        forecast_horizon (int): Positive forecast_horizon parameter.
        time_index (str): Column name of time_index parameter.
        exclude_featurizer (bool): Whether or not to exclude the TimeSeriesFeaturizer from
            the baseline graph. Defaults to False.
        series_id (str): Column name of series_id parameter. Only used for multiseries time series. Defaults to None.

    Returns:
        TimeSeriesPipelineBase, a time series pipeline corresponding to the problem type.

    """
    pipeline_class, pipeline_name = {
        ProblemTypes.TIME_SERIES_REGRESSION: (
            TimeSeriesRegressionPipeline,
            "Time Series Baseline Regression Pipeline",
        ),
        ProblemTypes.TIME_SERIES_MULTICLASS: (
            TimeSeriesMulticlassClassificationPipeline,
            "Time Series Baseline Multiclass Pipeline",
        ),
        ProblemTypes.TIME_SERIES_BINARY: (
            TimeSeriesBinaryClassificationPipeline,
            "Time Series Baseline Binary Pipeline",
        ),
        ProblemTypes.MULTISERIES_TIME_SERIES_REGRESSION: (
            MultiseriesRegressionPipeline,
            "Multiseries Time Series Baseline Pipeline",
        ),
    }[problem_type]
    baseline_estimator_name = (
        "Multiseries Time Series Baseline Regressor"
        if is_multiseries(problem_type)
        else "Time Series Baseline Estimator"
    )
    component_graph = [baseline_estimator_name]
    parameters = {
        "pipeline": {
            "time_index": time_index,
            "gap": gap,
            "max_delay": 0,
            "forecast_horizon": forecast_horizon,
        },
        baseline_estimator_name: {
            "gap": gap,
            "forecast_horizon": forecast_horizon,
        },
    }
    if is_multiseries(problem_type):
        parameters["pipeline"]["series_id"] = series_id
    if not exclude_featurizer:
        component_graph = ["Time Series Featurizer"] + component_graph
        parameters["Time Series Featurizer"] = {
            "max_delay": 0,
            "gap": gap,
            "forecast_horizon": forecast_horizon,
            "delay_target": True,
            "delay_features": False,
            "time_index": time_index,
        }
    baseline = pipeline_class(
        component_graph=component_graph,
        custom_name=pipeline_name,
        parameters=parameters,
    )
    return baseline


def rows_of_interest(
    pipeline,
    X,
    y=None,
    threshold=None,
    epsilon=0.1,
    sort_values=True,
    types="all",
):
    """Get the row indices of the data that are closest to the threshold. Works only for binary classification problems and pipelines.

    Args:
        pipeline (PipelineBase): The fitted binary pipeline.
        X (ww.DataTable, pd.DataFrame): The input features to predict on.
        y (ww.DataColumn, pd.Series, None): The input target data,  if available. Defaults to None.
        threshold (float): The threshold value of interest to separate positive and negative predictions. If None, uses the pipeline threshold if set, else 0.5. Defaults to None.
        epsilon (epsilon): The difference between the probability and the threshold that would make the row interesting for us. For instance, epsilon=0.1 and threhsold=0.5 would mean
            we consider all rows in [0.4, 0.6] to be of interest. Defaults to 0.1.
        sort_values (bool): Whether to return the indices sorted by the distance from the threshold, such that the first values are closer to the threshold and the later values are further. Defaults to True.
        types (str): The type of rows to keep and return. Can be one of ['incorrect', 'correct', 'true_positive', 'true_negative', 'all']. Defaults to 'all'.

            'incorrect' - return only the rows where the predictions are incorrect. This means that, given the threshold and target y, keep only the rows which are labeled wrong.
            'correct' - return only the rows where the predictions are correct. This means that, given the threshold and target y, keep only the rows which are correctly labeled.
            'true_positive' - return only the rows which are positive, as given by the targets.
            'true_negative' - return only the rows which are negative, as given by the targets.
            'all' - return all rows. This is the only option available when there is no target data provided.

    Returns:
        The indices corresponding to the rows of interest.

    Raises:
        ValueError: If pipeline is not a fitted Binary Classification pipeline.
        ValueError: If types is invalid or y is not provided when types is not 'all'.
        ValueError: If the threshold is provided and is exclusive of [0, 1].
    """
    valid_types = ["incorrect", "correct", "true_positive", "true_negative", "all"]
    if types not in valid_types:
        raise ValueError(
            "Invalid arg for 'types'! Must be one of {}".format(valid_types),
        )

    if types != "all" and y is None:
        raise ValueError("Need an input y in order to use types {}".format(types))

    if (
        not isinstance(pipeline, BinaryClassificationPipeline)
        or not pipeline._is_fitted
    ):
        raise ValueError(
            "Pipeline provided must be a fitted Binary Classification pipeline!",
        )

    if threshold is not None and (threshold < 0 or threshold > 1):
        raise ValueError(
            "Provided threshold {} must be between [0, 1]".format(threshold),
        )

    if threshold is None:
        threshold = pipeline.threshold or 0.5

    # get predicted proba
    pred_proba = pipeline.predict_proba(X)
    pos_value_proba = pred_proba.iloc[:, -1]
    preds = pos_value_proba >= threshold
    preds_value_proba = abs(pos_value_proba - threshold)

    # placeholder for y if it isn't supplied
    y_current = y if y is not None else preds

    # logic for breaking apart the different categories
    mask = y_current
    if types in ["correct", "incorrect"]:
        mask = preds == y
    mask = mask.astype(bool)

    if types in ["correct", "true_positive"]:
        preds_value_proba = preds_value_proba[mask.values]
    elif types in ["incorrect", "true_negative"]:
        preds_value_proba = preds_value_proba[~mask.values]

    if sort_values:
        preds_value_proba = preds_value_proba.sort_values(kind="stable")

    preds_value_proba = preds_value_proba[preds_value_proba <= epsilon]
    return preds_value_proba.index.tolist()


def unstack_multiseries(
    X,
    y,
    series_id,
    time_index,
    target_name,
):
    """Converts multiseries data with one series_id column and one target column to one target column per series id.

    Datetime information will be preserved only as a column in X.

    Args:
        X (pd.DataFrame): Data of shape [n_samples, n_features].
        y (pd.Series): Target data.
        series_id (str): The column which identifies which series each row belongs to.
        time_index (str): Specifies the name of the column in X that provides the datetime objects.
        target_name (str): The name of the target column.

    Returns:
        pd.DataFrame, pd.DataFrame: The unstacked X and y data.
    """
    # Combine X and y to make it easier to unstack
    full_dataset = pd.concat([X, y.set_axis(X.index)], axis=1)

    # Get the total number of series, with their names
    series_id_unique = full_dataset[series_id].unique()

    # Perform the unstacking
    X_unstacked_cols = []
    y_unstacked_cols = []
    for s_id in series_id_unique:
        single_series = full_dataset[full_dataset[series_id] == s_id]

        # Save the time_index for alignment
        new_time_index = single_series[time_index]
        for column_name in full_dataset.columns.drop([time_index, series_id]):
            new_column = single_series[column_name]
            new_column.index = new_time_index
            new_column.name = f"{column_name}_{s_id}"

            if column_name == target_name:
                y_unstacked_cols.append(new_column)
            else:
                X_unstacked_cols.append(new_column)

    # Concatenate all the single series to reform dataframes
    y_unstacked = pd.concat(y_unstacked_cols, axis=1)
    if len(X_unstacked_cols) == 0:
        X_unstacked = pd.DataFrame(index=y_unstacked.index)
    else:
        X_unstacked = pd.concat(X_unstacked_cols, axis=1)

    # Reset the axes now that they've been unstacked, keep time info in X
    X_unstacked = X_unstacked.reset_index()
    y_unstacked = y_unstacked.reset_index(drop=True)

    return X_unstacked, y_unstacked


def stack_data(data, include_series_id=False, series_id_name=None, starting_index=None):
    """Stacks the given DataFrame back into a single Series, or a DataFrame if include_series_id is True.

    Should only be used for data that is expected to be a single series. To stack multiple unstacked columns,
    use `stack_X`.

    Args:
        data (pd.DataFrame): The data to stack.
        include_series_id (bool): Whether or not to extract the series id and include it in a separate columns
        series_id_name (str): If include_series_id is True, the series_id name to set for the column. The column
            will be named 'series_id' if this parameter is None.
        starting_index (int): The starting index to use for the stacked series. If None and the input index is numeric,
            the starting index will match that of the input data. If None and the input index is a DatetimeIndex, the
            index will be the input data's index repeated over the number of columns in the input data.

    Returns:
        pd.Series or pd.DataFrame: The data in stacked series form.
    """
    if data is None or isinstance(data, pd.Series):
        return data

    stacked_series = data.stack(0)

    # Extract the original column name
    series_id_with_name = stacked_series.index.droplevel()
    stacked_series.name = "_".join(series_id_with_name[0].split("_")[:-1])

    # If the index is the time index, keep it
    if not data.index.is_numeric() and starting_index is None:
        new_time_index = data.index.unique().repeat(len(data.columns))
    # Otherwise, set it to unique integers
    else:
        start_index = starting_index or data.index[0]
        new_time_index = pd.RangeIndex(
            start=start_index,
            stop=start_index + len(stacked_series),
        )
    stacked_series = stacked_series.set_axis(new_time_index)

    # Pull out the series id information, if requested
    if include_series_id:
        series_id_col = pd.Series(
            series_id_with_name.map(lambda col_name: col_name.split("_")[-1]),
            name=series_id_name or "series_id",
            index=stacked_series.index,
        )
        stacked_series = pd.concat([series_id_col, stacked_series], axis=1)
    return stacked_series


def stack_X(X, series_id_name, time_index, starting_index=None, series_id_values=None):
    """Restacks the unstacked features into a single DataFrame.

    Args:
        X (pd.DataFrame): The unstacked features.
        series_id_name (str): The name of the series id column.
        time_index (str): The name of the time index column.
        starting_index (int): The starting index to use for the stacked DataFrame. If None, the starting index
            will match that of the input data. Defaults to None.
        series_id_values (set, list): The unique values of a series ID, used to generate the index. If None, values will
            be generated from X column values. Required if X only has time index values and no exogenous values.
            Defaults to None.

    Returns:
        pd.DataFrame: The restacked features.
    """
    original_columns = set()
    series_ids = series_id_values or set()
    if series_id_values is None:
        for col in X.columns:
            if col == time_index:
                continue
            separated_name = col.split("_")
            original_columns.add("_".join(separated_name[:-1]))
            series_ids.add(separated_name[-1])

    if len(series_ids) == 0:
        raise ValueError(
            "Series ID values need to be passed in X column values or as a set with the `series_id_values` parameter.",
        )

    time_index_col = X[time_index].repeat(len(series_ids)).reset_index(drop=True)

    if len(original_columns) == 0:
        start_index = starting_index or X.index[0]
        stacked_index = pd.RangeIndex(
            start=start_index,
            stop=start_index + len(time_index_col),
        )
        time_index_col.index = stacked_index
        restacked_X = pd.DataFrame(
            {
                time_index: time_index_col,
                series_id_name: sorted(list(series_ids)) * len(X),
            },
            index=stacked_index,
        )
    else:
        restacked_X = []
        for i, original_col in enumerate(original_columns):
            # Only include the series id once (for the first column)
            include_series_id = i == 0
            subset_X = [col for col in X.columns if original_col in col]
            restacked_X.append(
                stack_data(
                    X[subset_X],
                    include_series_id=include_series_id,
                    series_id_name=series_id_name,
                    starting_index=starting_index,
                ),
            )

        restacked_X = pd.concat(restacked_X, axis=1)
        time_index_col.index = restacked_X.index
        restacked_X[time_index] = time_index_col

    return restacked_X
